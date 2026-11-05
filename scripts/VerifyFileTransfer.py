"""真实SSH文件闭环验收：用随机测试目录验证MCP文件工具的上传、下载和目录查询。

参数target为已有SSH别名，remote-root为已有远端测试父目录；仅创建带随机后缀的新目录。
不使用模型、不发送聊天消息、不删除用户文件。远端测试目录保留供人工核查。
"""
import argparse
import hashlib
import json
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fileTransfer.FileTransferService import FileTransferService
from fileTransfer.FileTransferTools import FileTransferTools
from fileTransfer.SshConnection import SshConnection
from remoteControl.RemoteTargetConfig import RemoteTargetConfig


class VerifyFileTransfer:
    """执行小文件真实链路验证，并输出可比对的内容散列。"""

    def run_checks(self, target, remote_root, output=None):
        """target/remote_root为已授权SSH目标及既有父目录；output为可选中文报告路径。"""
        config = RemoteTargetConfig(ROOT)
        tools = FileTransferTools(FileTransferService(config))
        remote_dir = remote_root.rstrip("/\\").replace("\\", "/") + "/FileTransferCheck-" + uuid.uuid4().hex
        with SshConnection(config, target) as sftp:
            sftp.mkdir(remote_dir)
        with tempfile.TemporaryDirectory(prefix="SynLoveFileCheck-") as folder:
            source = Path(folder) / "中文 源文件.bin"
            received = Path(folder) / "中文 返回文件.bin"
            data = bytes(range(256)) * 256 + "欣爱双向文件传输验证".encode("utf-8")
            source.write_bytes(data)
            upload = tools.call_tool("remote_file_transfer", {"source": "local", "sourcePath": str(source),
                "target": target, "targetPath": remote_dir + "/中文 空格文件.bin"})
            download = tools.call_tool("remote_file_transfer", {"source": target,
                "sourcePath": upload["targetPath"], "target": "local", "targetPath": str(received)})
            if received.read_bytes() != data:
                raise ValueError("上传下载闭环字节不一致")
            listing = tools.call_tool("remote_file_list", {"target": target, "path": remote_dir})
            if not any(item["name"] == "中文 空格文件.bin" for item in listing["entries"]):
                raise ValueError("目录查询未找到测试文件")
            rejected = False
            try:
                tools.call_tool("remote_file_transfer", {"source": "local", "sourcePath": str(source),
                    "target": target, "targetPath": upload["targetPath"]})
            except ValueError:
                rejected = True
            if not rejected:
                raise ValueError("默认禁止覆盖未生效")
            report = {"ok": True, "target": target, "remoteTestDirectory": remote_dir,
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "uploadVerified": upload["verified"], "downloadVerified": download["verified"],
                "directoryListed": True, "overwriteRejected": rejected}
        if output:
            path = Path(output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    VerifyFileTransfer().run_checks(args.target, args.remote_root, args.output)
