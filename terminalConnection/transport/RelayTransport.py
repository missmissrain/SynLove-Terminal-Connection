"""终端到服务器传输：复用已有VPS任务API，只重查已提交任务，不重放业务指令。

注册参数config含relayURL、relayToken、tlsCaFile；必须TLS校验，测试可显式允许回环HTTP。
"""
import json
import ssl
import time
import urllib.request


class RelayTransport:
    """将业务信封提交给唯一终端管理能力，保持设备侧持久幂等。"""

    def __init__(self, config):
        """config为私有连接配置；默认能力terminal.connection，最大等待40秒。"""
        self.config = config
        self.base = config["relayURL"].rstrip("/")
        if not self.base.startswith("https://") and not config.get("allowLocalTest"):
            raise ValueError("生产中转必须使用HTTPS")
        self.context = ssl.create_default_context()
        if config.get("tlsCaFile"):
            self.context.load_verify_locations(cafile=config["tlsCaFile"])
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=self.context))
        for handler in self.opener.handlers:
            if isinstance(handler, urllib.request.HTTPRedirectHandler):
                handler.redirect_request = lambda *args, **kwargs: None

    def request_json(self, method, path, data=None):
        """method/path为固定VPS接口；响应上限1MiB，不在异常中打印令牌或正文。"""
        request = urllib.request.Request(self.base + path,
            data=None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.config["relayToken"],
                     "Content-Type": "application/json"}, method=method)
        with self.opener.open(request, timeout=15) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("中转响应过大")
            return json.loads(raw)

    def exchange_message(self, envelope):
        """envelope为已签名业务或一次性配对；仅创建一个任务，之后查询同一ID。"""
        view = self.request_json("POST", "/tasks", {"capability": self.config.get("capability", "terminal.connection"),
            "input": envelope, "timeoutMs": 45000})
        view = view.get("task", view)
        key = view["id"]
        deadline = time.monotonic() + 40
        while view.get("status") not in ("done", "failed", "cancelled", "expired"):
            if time.monotonic() > deadline:
                raise TimeoutError("终端中转任务等待超时；业务记录仍保留")
            time.sleep(0.25)
            view = self.request_json("GET", "/tasks/" + key)
        if view.get("status") != "done":
            raise RuntimeError("服务器终端管理服务未确认接收")
        return view.get("payload")
