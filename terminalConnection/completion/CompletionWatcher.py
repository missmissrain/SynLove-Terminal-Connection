"""终端编码完成观测：只读Codex/OpenCode本机记录，变更时将摘要写入待发箱。

注册参数journal为终端持久库；首次只建基线，默认新鲜度两小时，不在终端调用模型或QQ。
"""
import hashlib
import json
import time
from terminalConnection.completion.CodexSource import CodexSource
from terminalConnection.completion.OpencodeSource import OpencodeSource
from terminalConnection.completion.NoticeText import NoticeText


class CompletionWatcher:
    """每台终端独立观测自己的编码任务，由服务器统一决定是否推送。"""

    def __init__(self,journal):
        """journal为本机唯一日志；创建只读来源，不修改编码器会话。"""
        self.journal=journal
        self.sources={"codex":CodexSource(),"opencode":OpencodeSource()}
        self.errors={}
        with self.journal.lock, self.journal.db:
            self.journal.db.execute("CREATE TABLE IF NOT EXISTS watcherState(id TEXT PRIMARY KEY,value REAL)")

    def available_capabilities(self):
        """无参数；只在本机存在对应会话源时声明编码完成观测能力。"""
        values=[]
        if (self.sources["codex"].codex_home/"state_5.sqlite").is_file():
            values.append("coding.codex")
        if (self.sources["opencode"].data_dir/"opencode.db").is_file():
            values.append("coding.opencode")
        return values

    def scan_sources(self,settings):
        """settings为服务器通知开关；首条基线及关闭期间仅更新指纹，不补发历史。"""
        for name,source in self.sources.items():
            try:
                reports=source.poll()
                self.errors.pop(name,None)
            except Exception as exc:
                self.errors[name]=type(exc).__name__
                continue
            with self.journal.lock, self.journal.db:
                baseline = self.journal.db.execute("SELECT value FROM watcherState WHERE id=?", (name,)).fetchone()
                if baseline is None:
                    self.journal.db.execute("INSERT INTO watcherState VALUES(?,?)", (name, time.time()))
            for report in reports:
                key=name+":"+report.task_id
                with self.journal.lock,self.journal.db:
                    prior=self.journal.db.execute("SELECT value FROM fingerprints WHERE id=?",(key,)).fetchone()
                    if prior and prior["value"]==report.fingerprint:
                        continue
                    self.journal.db.execute("INSERT OR REPLACE INTO fingerprints VALUES(?,?)",(key,report.fingerprint))
                    if baseline is None or not settings.get(name,False) or report.title.startswith("[SynLoveTask:"):
                        continue
                    if report.status not in ("已完成","已失败","已中止") or not 0<=time.time()-report.finished_at<=7200:
                        continue
                    if prior is None and report.finished_at < baseline["value"]:
                        continue
                    ident=hashlib.sha256((key+":"+report.fingerprint).encode()).hexdigest()
                    text = NoticeText.clean_text(report.fallback_body(6000))
                    if not text:
                        continue
                    value={"id":ident,"source":name,"title":report.title[:200],
                        "text":text,"finishedAt":report.finished_at}
                    self.journal.db.execute("INSERT OR IGNORE INTO events VALUES(?,?)",
                        (ident,json.dumps(value,ensure_ascii=False)))
