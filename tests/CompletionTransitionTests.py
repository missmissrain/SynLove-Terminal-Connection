"""完成通知转换测试：验证首次观测已完成的新任务不会丢失，重启不补发历史，受管子任务不重复通知。"""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from terminalConnection.client.TerminalJournal import TerminalJournal
from terminalConnection.completion.CompletionWatcher import CompletionWatcher


class CompletionTransitionTests(unittest.TestCase):
    """使用真实本机持久库检查待发事件，不调用模型或平台。"""

    def test_new_completed_task_restart_and_managed_task(self):
        """无参数；首轮建基线，后续首次出现的完成任务应入队，旧任务和受管任务应跳过。"""
        with tempfile.TemporaryDirectory() as folder:
            journal = TerminalJournal(Path(folder) / "client.sqlite")
            try:
                watcher = CompletionWatcher(journal)
                reports = []
                watcher.sources = {"codex": SimpleNamespace(poll=lambda: reports)}
                watcher.scan_sources({"codex": True})
                stamp = time.time()
                reports.append(SimpleNamespace(task_id="new", fingerprint="done", title="newTask",
                    status="已完成", finished_at=stamp, fallback_body=lambda size: "真实最终摘要"))
                watcher.scan_sources({"codex": True})
                self.assertEqual(len(journal.pending_items()[0]), 1)
                watcher = CompletionWatcher(journal)
                reports.extend([
                    SimpleNamespace(task_id="old", fingerprint="done", title="oldTask", status="已完成",
                        finished_at=stamp-100, fallback_body=lambda size: "历史"),
                    SimpleNamespace(task_id="managed", fingerprint="done", title="[SynLoveTask:abc]",
                        status="已完成", finished_at=stamp, fallback_body=lambda size: "受管任务"),
                ])
                watcher.sources = {"codex": SimpleNamespace(poll=lambda: reports)}
                watcher.scan_sources({"codex": True})
                self.assertEqual(len(journal.pending_items()[0]), 1)
            finally:
                journal.db.close()
