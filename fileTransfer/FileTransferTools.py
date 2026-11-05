"""文件MCP工具契约：目标列表、目录浏览、双向传输；只回传元数据和校验结果。

注册参数service为FileTransferService；模型不可传入地址、端口、账户或密钥。
"""


class FileTransferTools:
    """负责严格校验MCP文件工具参数，不将原始文件字节送入模型上下文。"""

    def __init__(self, service):
        """service为受信配置驱动的文件传输服务。"""
        self.service = service

    def list_tools(self):
        """无参数；返回稳定工具定义，当前主机固定用local，远程设备必须先列举。"""
        target = {"type": "string", "description": "设备列表的fileTargets中返回的SSH别名或已绑定设备ID；local只表示此MCP所在机器"}
        path = {"type": "string", "description": "设备上的绝对路径，Windows如C:/目录/文件；不自动创建父目录"}
        return [
            {"name": "remote_file_targets", "description": "列出可用文件目标配置，与设备列表配合确认目标。local是当前运行欣爱的MCP机器；列举不代表网络已验证。",
             "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "remote_file_list", "description": "通过已配置SSH/SFTP浏览指定设备目录，最多返回200项，不读取文件正文。",
             "inputSchema": {"type": "object", "properties": {"target": target, "path": path},
                             "required": ["target", "path"], "additionalProperties": False}},
            {"name": "remote_file_transfer",
             "description": "双向传输文件：终端到服务器、服务器到终端或终端间经当前机器流式转发。先查设备与fileTargets，准确匹配用户所指设备。只传已授权文件，默认不覆盖；SHA-256验证后原子提交，失败不自动重试。文件内容不进入模型上下文。",
             "inputSchema": {"type": "object", "properties": {"source": target, "sourcePath": path,
                 "target": target, "targetPath": path, "overwrite": {"type": "boolean", "default": False},
                 "timeoutSec": {"type": "integer", "minimum": 10, "maximum": 300, "default": 180}},
                 "required": ["source", "sourcePath", "target", "targetPath"], "additionalProperties": False}}
        ]

    def call_tool(self, name, args):
        """name/args为固定工具和参数；校验失败不连接网络，结果只含操作元数据。"""
        schema = {tool["name"]: tool["inputSchema"] for tool in self.list_tools()}.get(name)
        if schema is None or not isinstance(args, dict) or set(args) - set(schema["properties"]):
            raise ValueError("文件工具名或参数无效")
        if any(key not in args for key in schema.get("required", [])):
            raise ValueError("文件工具缺少必要参数")
        if name == "remote_file_targets":
            return {"ok": True, "fileTargets": self.service.list_targets()}
        if name == "remote_file_list":
            return self.service.list_files(args["target"], args["path"])
        return self.service.transfer_file(args["source"], args["sourcePath"], args["target"],
            args["targetPath"], args.get("overwrite", False), args.get("timeoutSec", 180))
