"""终端MCP：查询登记设备、远控、音乐与SSH文件搬运；复用私有目标配置，不回传凭据。

参数config为服务器管理员JSON；保留remote_cmd工具别名以兼容主程序现有工具描述。
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from terminalConnection.client.TerminalApiClient import TerminalApiClient
from remoteControl.RemoteTargetConfig import RemoteTargetConfig
from fileTransfer.FileTransferService import FileTransferService
from fileTransfer.FileTransferTools import FileTransferTools


class TerminalMcp:
    """向欣爱提供设备列表与按能力执行的统一工具。"""

    def __init__(self,config):
        """config为管理员配置路径；列举不暴露IP、公钥和令牌。"""
        self.client=TerminalApiClient(config)
        # 桥接配置负责定位私有维护仓库；工具只接受预先登记的目标别名。
        root = self.client.config.get("maintenanceRoot") or self.client.config.get("repositoryRoot")
        if root:
            base = Path(config).resolve().parent.parent if not isinstance(config, dict) else Path.cwd()
            root = (base / root).resolve()
        else:
            root = Path(__file__).resolve().parents[2]
        self.files=FileTransferTools(FileTransferService(RemoteTargetConfig(root)))

    def list_tools(self):
        """无参数；给出兼容旧工具名与显式终端工具名的稳定协议。"""
        target={"type":"string","description":"设备列表返回的设备ID，不要猜测"}
        return [
            {"name":name,"description":"列出已登记设备、在线状态、名称、类型与可用能力。",
             "inputSchema":{"type":"object","properties":{}}} for name in ("terminal_devices","remote_cmd_list")
        ]+[
            {"name":"remote_cmd_probe","description":"检查指定终端的实际签名通信与执行器。",
             "inputSchema":{"type":"object","properties":{"target":target},"required":["target"]}},
            {"name":"remote_cmd_run","description":"在明确选中的在线设备执行已授权命令；先查看设备能力，超时不重放。",
             "inputSchema":{"type":"object","properties":{"target":target,"command":{"type":"string"},
                 "timeoutSec":{"type":"integer","minimum":1,"maximum":120}},"required":["target","command"]}},
            {"name":"terminal_music","description":"控制指定设备的音乐：查询、暂停、继续、停止。点歌仍使用酷狗工具。",
             "inputSchema":{"type":"object","properties":{"target":target,
                 "action":{"type":"string","enum":["status","pause","resume","stop"]}},"required":["target","action"]}}
        ]+self.files.list_tools()

    def handle_message(self,message):
        """message为JSON-RPC；调用失败返回isError，不伪造设备执行成功。"""
        if not isinstance(message, dict):
            return {"jsonrpc":"2.0","id":None,"error":{"code":-32600,"message":"Invalid Request"}}
        key=message.get("id")
        if key is None:
            return None
        try:
            method=message.get("method")
            if method=="initialize":
                result={"protocolVersion":"2025-06-18","capabilities":{"tools":{}},
                    "serverInfo":{"name":"synlove-terminal-connection","version":"1.0.0"}}
            elif method=="ping":
                result={}
            elif method=="tools/list":
                result={"tools":self.list_tools()}
            elif method=="tools/call":
                params=message.get("params") or {}
                args=params.get("arguments") or {}
                if not isinstance(params,dict) or not isinstance(args,dict):
                    raise ValueError("工具参数必须为对象")
                name=params.get("name")
                if name in ("terminal_devices","remote_cmd_list"):
                    devices=self.client.request_json("GET","/v1/devices")["devices"]
                    value={"devices":devices,"targets":[{"target":d["id"],"description":d["name"],
                        "enabled":bool(d["enabled"]),"online":d["online"],"capabilities":d["capabilities"]} for d in devices]}
                    value["fileTargets"]=self.files.service.list_targets()
                elif name in {tool["name"] for tool in self.files.list_tools()}:
                    value=self.files.call_tool(name,args)
                elif name=="remote_cmd_probe":
                    value=self.client.execute_command(args["target"],{"type":"probe"})
                elif name=="remote_cmd_run":
                    timeout=args.get("timeoutSec",30)
                    if type(timeout) is not int or not 1<=timeout<=120:
                        raise ValueError("执行期限须为1至120秒整数")
                    value=self.client.execute_command(args["target"],{"type":"cmd","params":{
                        "command":args["command"],"timeoutSec":timeout}},wait_seconds=125)
                elif name=="terminal_music":
                    value=self.client.execute_command(args["target"],{"type":"music","params":{"action":args["action"]}})
                else:
                    raise ValueError("未知终端工具")
                result={"content":[{"type":"text","text":json.dumps(value,ensure_ascii=False)}],
                        "isError":value.get("ok") is False}
            else:
                raise ValueError("不支持的方法")
            return {"jsonrpc":"2.0","id":key,"result":result}
        except Exception as exc:
            error = (str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError)
                     else type(exc).__name__)
            return {"jsonrpc":"2.0","id":key,"result":{"isError":True,
                "content":[{"type":"text","text":json.dumps({"ok":False,
                    "error":"终端请求失败："+error},ensure_ascii=False)}]}}

    def run_stdio(self):
        """无参数；每行一个JSON-RPC，stdout仅用于协议。"""
        for line in sys.stdin:
            try:
                result=self.handle_message(json.loads(line))
                if result:
                    print(json.dumps(result,ensure_ascii=False),flush=True)
            except ValueError:
                continue


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True)
    sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    sys.stderr.reconfigure(encoding="utf-8",errors="replace")
    TerminalMcp(parser.parse_args().config).run_stdio()
