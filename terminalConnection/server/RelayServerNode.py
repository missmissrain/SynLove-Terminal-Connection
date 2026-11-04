"""服务器终端管理中转节点：主动WSS连接现有VPS，仅注册terminal.connection能力。

注册参数manager为业务管理器，config含relayURL/relayToken/tlsCaFile；不改变VPS原模型服务。
"""
import json
import ssl
import threading
import time
from urllib.parse import urlsplit, urlunsplit
import websocket


class RelayServerNode:
    """处理已有Hub派单、心跳和自动重连，设备验签由管理器执行。"""

    def __init__(self, manager, config):
        """manager/config为终端业务及私有中转配置；只使用一个长连接。"""
        self.manager,self.config = manager,config
        self.connected = False
        self.last_error = ""
        self.stopped = threading.Event()

    def run_forever(self):
        """无参数；有界退避重连，不输出连接URL、令牌或终端报文。"""
        parsed = urlsplit(self.config["relayURL"])
        url = urlunsplit(("wss" if parsed.scheme=="https" else "ws",parsed.netloc,"/ws/node","",""))
        delay = 1
        while not self.stopped.is_set():
            sock = None
            try:
                options = {"cert_reqs":ssl.CERT_REQUIRED,"check_hostname":True}
                if self.config.get("tlsCaFile"):
                    options["ca_certs"] = self.config["tlsCaFile"]
                sock = websocket.create_connection(url,timeout=12,
                    header=["Authorization: Bearer "+self.config["relayToken"]],sslopt=options,
                    http_no_proxy=["*"])
                sock.send(json.dumps({"type":"hello","role":"node","nodeId":"synlove-terminal-manager",
                    "name":"SynLove Terminal Manager","heartbeatSeconds":10,
                    "capabilities":[{"name":"terminal.connection","concurrency":16,"priority":10}]}))
                last_ping = time.monotonic()
                last_received = last_ping
                sock.settimeout(2)
                while not self.stopped.is_set():
                    if time.monotonic()-last_received>35:
                        raise TimeoutError("中转心跳未返回")
                    if time.monotonic()-last_ping>10:
                        sock.send(json.dumps({"type":"ping","ts":int(time.time()*1000)}))
                        last_ping=time.monotonic()
                    try:
                        raw=sock.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not raw:
                        raise ConnectionError("中转连接关闭")
                    last_received=time.monotonic()
                    frame=json.loads(raw)
                    if frame["type"]=="hello.ok":
                        self.connected=True
                        self.last_error=""
                        delay=1
                    elif frame["type"]=="ping":
                        sock.send(json.dumps({"type":"pong","ts":frame.get("ts")}))
                    elif frame["type"]=="task":
                        base={"taskId":frame["taskId"],"generation":frame["generation"]}
                        sock.send(json.dumps({"type":"ack",**base,"accepted":True}))
                        try:
                            value=self.manager.receive_message(frame.get("input") or {})
                            result={"type":"result",**base,"ok":True,"payload":value,"attachments":[]}
                        except Exception as exc:
                            result={"type":"result",**base,"ok":False,"error":"终端报文拒绝："+type(exc).__name__}
                        sock.send(json.dumps(result,ensure_ascii=False))
            except Exception as exc:
                self.connected=False
                self.last_error=type(exc).__name__
            finally:
                self.connected=False
                if sock:
                    sock.close()
            self.stopped.wait(delay)
            delay=min(delay*2,15)
