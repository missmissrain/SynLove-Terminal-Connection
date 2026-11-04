"""完成源兼容回归：用真实格式的OpenCode SQLite确认最终回复、工具暂停、失败和取消边界。"""
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from terminalConnection.completion.NoticeText import NoticeText
from terminalConnection.completion.OpencodeSource import OpencodeSource


class CompletionSourceTests(unittest.TestCase):
    """隔离临时会话数据，检查迁移后的解析器而不读取真实聊天。"""

    def setUp(self):
        """无参数；创建OpenCode v2数据库与独立会话。"""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "opencode.db"
        self.now = int(time.time() * 1000) - 30000
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("CREATE TABLE session_v2(id TEXT,directory TEXT,title TEXT,time_updated INTEGER,time_archived INTEGER,parent_id TEXT);"
                "CREATE TABLE session_message(id TEXT,session_id TEXT,type TEXT,seq INTEGER,time_created INTEGER,data TEXT);")
            db.execute("INSERT INTO session_v2 VALUES(?,?,?,?,NULL,NULL)", ("session", str(self.root), "测试", self.now))
        self.source = OpencodeSource(self.root)

    def tearDown(self):
        """无参数；清理测试数据。"""
        self.temp.cleanup()

    def add_message(self, seq, kind, data):
        """seq为序号，kind为事件类型，data为真实JSON结构；写入一个隔离事件。"""
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO session_message VALUES(?,?,?,?,?,?)",
                (str(seq), "session", kind, seq, self.now, json.dumps(data, ensure_ascii=False)))

    def complete_turn(self, outcome="succeeded"):
        """outcome为终态；加入中间工具输出、推理和最终答案，供解析器区分。"""
        self.add_message(1, "user", {"text": "测试任务"})
        self.add_message(2, "assistant", {"finish": "tool-calls", "time": {"completed": self.now},
            "content": [{"type": "text", "text": "中间输出"}]})
        self.add_message(3, "assistant", {"finish": "stop", "time": {"completed": self.now},
            "content": [{"type": "reasoning", "text": "内部推理"}, {"type": "text", "text": "最终答案"}]})
        self.add_message(4, "idle", {"outcome": outcome})

    def test_final_and_stable_fingerprint(self):
        """无参数；只取最终答案，浏览会话更新时间不能制造新完成事件。"""
        self.complete_turn()
        report, = self.source.poll()
        self.assertEqual((report.status, report.result), ("已完成", "最终答案"))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE session_v2 SET time_updated=?", (self.now + 20000,))
        self.assertEqual(self.source.poll()[0].fingerprint, report.fingerprint)

    def test_new_user_invalidates_old_completion(self):
        """无参数；新用户轮次开始后，不继续广播旧答案。"""
        self.complete_turn()
        self.add_message(5, "user", {"text": "新任务"})
        self.assertEqual(self.source.poll(), [])

    def test_read_connection_closes_on_success_and_error(self):
        """无参数；真实连接在查询成功和解析异常后均立即关闭，不能依赖垃圾回收释放库文件。"""
        connect = sqlite3.connect
        for fails in (False, True):
            with self.subTest(fails=fails):
                connections = []

                def open_connection(*args, **kwargs):
                    """args/kwargs为SQLite连接参数；保存真实连接以检查查询结束后的资源状态。"""
                    conn = connect(*args, **kwargs)
                    connections.append(conn)
                    return conn

                with patch("terminalConnection.completion.OpencodeSource.sqlite3.connect",
                           side_effect=open_connection):
                    if fails:
                        with patch.object(self.source, "_poll_v2", side_effect=ValueError("模拟解析异常")):
                            with self.assertRaises(ValueError):
                                self.source.poll()
                    else:
                        self.assertEqual(self.source.poll(), [])
                self.assertEqual(len(connections), 1)
                with self.assertRaises(sqlite3.ProgrammingError):
                    connections[0].execute("SELECT 1")

    def test_tool_pause_is_not_completion(self):
        """无参数；工具调用加idle不构成最终回答。"""
        self.add_message(1, "user", {"text": "任务"})
        self.add_message(2, "assistant", {"finish": "tool-calls", "time": {"completed": self.now},
            "content": [{"type": "text", "text": "稍等"}]})
        self.add_message(3, "idle", {"outcome": "succeeded"})
        self.assertEqual(self.source.poll(), [])

    def test_failure_and_cancellation(self):
        """无参数；失败不能误报为成功，取消与失败分别表达。"""
        self.add_message(1, "user", {"text": "任务"})
        self.add_message(2, "idle", {"outcome": "failed"})
        report, = self.source.poll()
        self.assertEqual(report.status, "已失败")
        self.assertIn("未产生最终回复", report.result)
        self.add_message(3, "idle", {"outcome": "cancelled"})
        self.assertEqual(self.source.poll()[0].status, "已中止")

    def test_receipt_questions_removed_decisions_kept(self):
        """无参数；清理无意义收件问题，不删除真正需用户确认的内容。"""
        self.assertEqual(NoticeText.clean_text("任务完成。你那边 QQ 收到结果消息了吗？"), "任务完成。")
        self.assertEqual(NoticeText.clean_text("能收到消息吗？"), "")
        self.assertEqual(NoticeText.clean_text("需要删除旧文件吗？"), "需要删除旧文件吗？")
