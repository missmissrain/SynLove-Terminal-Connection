"""主进程与工具使用的终端管理客户端：从私有配置加载本机服务器API，不持有终端私钥。

注册参数config为文件路径或已加载对象；API默认回环17864，不自动重试写请求。
"""
import json
import time
import urllib.request
from pathlib import Path


class TerminalApiClient:
    """将模型工具和GUI统一连接至设备状态的唯一来源。"""

    def __init__(self, config):
        """config为管理员配置或桥接配置，含adminURL与adminToken。"""
        self.config=json.loads(Path(config).read_text(encoding="utf-8-sig")) if not isinstance(config,dict) else config

    def request_json(self,method,path,data=None):
        """method/path/data为固定API与JSON正文；禁止代理及重定向，不输出认证令牌。"""
        request=urllib.request.Request(self.config.get("adminURL","http://127.0.0.1:17864")+path,
            data=None if data is None else json.dumps(data,ensure_ascii=False).encode(),
            headers={"Authorization":"Bearer "+self.config["adminToken"],"Content-Type":"application/json"},method=method)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for handler in opener.handlers:
            if isinstance(handler,urllib.request.HTTPRedirectHandler):
                handler.redirect_request=lambda *args,**kwargs:None
        with opener.open(request,timeout=8) as response:
            value=json.loads(response.read(1024*1024))
        if not value.get("ok"):
            raise RuntimeError("终端管理拒绝请求")
        return value

    def execute_command(self,device,command,wait_seconds=35):
        """device为设备ID，command为类型化指令；只提交一次，超时保留指令ID供后查。"""
        key=self.request_json("POST","/v1/commands",{"deviceId":device,"command":command})["id"]
        deadline=time.monotonic()+wait_seconds
        while time.monotonic()<deadline:
            value=self.request_json("GET","/v1/commands/"+key)
            if value["state"]=="done":
                return value["result"]
            time.sleep(0.3)
        return {"ok":False,"pending":True,"requestId":key,"error":"指令已提交，尚未确认完成；不要重复执行"}
