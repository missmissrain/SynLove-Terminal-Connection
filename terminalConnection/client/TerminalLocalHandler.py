"""终端本机接口：复用音乐协议，增加编码完成事件收件；仅回环监听并验证本机令牌。

注册参数由TerminalAgent设置；POST /v1/notify接受codex/opencode摘要，不接受外部服务器命令。
"""
import hmac
import json
from terminalConnection.music.MusicRequestHandler import MusicRequestHandler


class TerminalLocalHandler(MusicRequestHandler):
    """将工具钩子接入本机持久待发箱，保留音乐接口兼容性。"""

    def do_POST(self):
        """无参数；音乐交给既有协议，完成通知校验后持久保存。"""
        if self.path!="/v1/notify":
            if not self.server.player:
                self.send_json(503,{"ok":False,"error":"本机音乐能力未配置"})
                return
            return super().do_POST()
        if not hmac.compare_digest(self.headers.get("Authorization","").encode(),
                                   ("Bearer "+self.server.token).encode()):
            self.send_json(401,{"ok":False,"error":"未授权"})
            return
        try:
            length=int(self.headers.get("Content-Length",0))
            if not 0<length<=30000:
                raise ValueError("通知大小无效")
            event=json.loads(self.rfile.read(length))
            if event.get("source") not in ("codex","opencode") or not isinstance(event.get("text"),str):
                raise ValueError("通知来源无效")
            key=self.server.agent.journal.add_event(event)
            self.send_json(200,{"ok":True,"id":key,"queued":True})
        except Exception:
            self.send_json(400,{"ok":False,"error":"通知格式无效"})
