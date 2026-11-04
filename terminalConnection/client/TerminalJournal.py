"""终端持久日志：保存事件待发箱、命令执行记录和编码完成指纹；断网不丢已落盘事件。

注册参数path为终端私有SQLite路径；命令执行前先登记，重复ID从不再次执行。
"""
import hashlib
import json
import re
import sqlite3
import threading


class TerminalJournal:
    """终端本地执行与上报状态的唯一持久来源。"""

    def __init__(self,path):
        """path为已创建父目录下的SQLite文件；初始化WAL和并发锁。"""
        self.lock=threading.RLock()
        self.db=sqlite3.connect(str(path),check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,body TEXT);
        CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,body TEXT,state TEXT,result TEXT,acked INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS fingerprints(id TEXT PRIMARY KEY,value TEXT);
        """)
        # 上次运行中崩溃的指令结果未知，禁止在重启后重新执行。
        self.db.execute("UPDATE commands SET state='done',result=? WHERE state='running'",
            (json.dumps({"ok":False,"unknown":True,"error":"终端重启，执行结果未知；未重新执行"}),))
        self.db.commit()

    def add_event(self,event):
        """event为来源、标题、摘要等内容；没有ID时按稳定正文哈希去重。"""
        if (not isinstance(event,dict) or event.get("source") not in ("codex","opencode")
            or not isinstance(event.get("text"),str) or not isinstance(event.get("title",""),str)):
            raise ValueError("通知来源或正文无效")
        value=dict(event)
        value.setdefault("id",hashlib.sha256(json.dumps(event,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
        if (not isinstance(value["id"],str) or not re.fullmatch("[a-f0-9]{64}",value["id"])
            or len(json.dumps(value,ensure_ascii=False).encode("utf-8"))>30000):
            raise ValueError("通知标识或大小无效")
        with self.lock,self.db:
            self.db.execute("INSERT OR IGNORE INTO events VALUES(?,?)",
                (value["id"],json.dumps(value,ensure_ascii=False)))
        return value["id"]

    def pending_items(self):
        """无参数；优先命令结果，每类最多20项且总量400KiB，避免Base64后超过中转上限。"""
        with self.lock:
            events,results=[],[]
            budget=400*1024
            for row in self.db.execute("SELECT id,result FROM commands WHERE state='done' AND acked=0 LIMIT 20"):
                value={"id":row["id"],"result":json.loads(row["result"])}
                size=len(json.dumps(value,ensure_ascii=False).encode("utf-8"))+2
                if size>budget:
                    break
                results.append(value)
                budget-=size
            for row in self.db.execute("SELECT body FROM events LIMIT 20"):
                size=len(row["body"].encode("utf-8"))+2
                if size>budget:
                    break
                events.append(json.loads(row["body"]))
                budget-=size
            return events,results

    def acknowledge_items(self,events,results):
        """events/results为服务器已确认的ID；只在签名确认之后删除待发或标记已确认。"""
        with self.lock,self.db:
            for key in events:
                self.db.execute("DELETE FROM events WHERE id=?",(key,))
            for key in results:
                self.db.execute("UPDATE commands SET acked=1 WHERE id=?",(key,))

    def claim_command(self,command):
        """command为服务器指令；事务抢占执行权，同一ID不允许内容变更或重复执行。"""
        key=command["id"]
        body=json.dumps(command,sort_keys=True)
        with self.lock,self.db:
            old=self.db.execute("SELECT body FROM commands WHERE id=?",(key,)).fetchone()
            if old:
                if old["body"]!=body:
                    raise ValueError("同一指令ID内容变化")
                return False
            self.db.execute("INSERT INTO commands(id,body,state) VALUES(?,?,'running')",(key,body))
            return True

    def finish_command(self,key,result):
        """key为已抢占指令，result为有界执行结果；落盘后交由心跳上报。"""
        with self.lock,self.db:
            self.db.execute("UPDATE commands SET state='done',result=? WHERE id=?",
                (json.dumps(result,ensure_ascii=False),key))
