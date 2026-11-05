"""双向文件搬运：在当前MCP机器与登记SSH设备之间流式复制，支持远端到远端。

注册参数config为RemoteTargetConfig；最大单文件默认256MiB、块大小256KiB，
只把路径和校验结果返回模型。默认180秒总期限，禁止覆盖，连接无自动重试。
"""
import errno
import hashlib
import os
import stat
import time
from contextlib import ExitStack

from fileTransfer.FileEndpoint import FileEndpoint


class FileTransferService:
    """把设备别名解析、端点连接、完整性校验与原子提交组织成一次传输。"""

    def __init__(self, config, connection_factory=None, max_bytes=256 * 1024 * 1024):
        """config为固定目标配置；factory用于测试替身；max_bytes限制单文件大小。"""
        self.config = config
        self.factory = connection_factory
        self.max_bytes = max_bytes

    def list_targets(self):
        """无参数；列举当前机器和显式SSH目标，不探测网络、不回传地址或凭据。"""
        result = [{"target": "local", "description": "当前运行此MCP的机器（生产环境为服务器）",
                   "enabled": True, "transport": "local", "connectionVerified": False}]
        for name, item in self.config.read_config().get("targets", {}).items():
            if isinstance(item, dict):
                result.append({"target": name, "deviceId": item.get("deviceId", ""),
                    "description": item.get("description", ""), "enabled": item.get("enabled") is True,
                    "transport": "ssh-sftp", "connectionVerified": False})
        return result

    def resolve_target(self, target):
        """target为列表返回的SSH别名或绑定设备ID；只解析明确登记项。"""
        if not isinstance(target, str) or not target:
            raise ValueError("必须指定文件目标，请先列出设备和文件目标")
        if target == "local":
            return target
        items = self.config.read_config().get("targets", {})
        if target in items:
            return target
        matches = [name for name, item in items.items()
                   if isinstance(item, dict) and item.get("deviceId") == target and item.get("enabled") is True]
        if len(matches) != 1:
            raise ValueError("设备尚未绑定唯一的已启用SSH文件目标，请先列出文件目标")
        return matches[0]

    def connect_endpoint(self, stack, target, timeout):
        """stack为资源回收栈；target为规范别名，timeout为剩余读写期限。"""
        if target == "local":
            return FileEndpoint()
        # SSH是可选能力；缺少Paramiko不能让设备列表、音乐与通知整个MCP加载失败。
        factory = self.factory
        if factory is None:
            from fileTransfer.SshConnection import SshConnection
            factory = SshConnection
        return FileEndpoint(stack.enter_context(factory(self.config, target, timeout=min(20, timeout))))

    def list_files(self, target, path):
        """target/path为明确设备与绝对目录；最多列200项，未配置设备不建立连接。"""
        target = self.resolve_target(target)
        with ExitStack() as stack:
            endpoint = self.connect_endpoint(stack, target, 20)
            path = endpoint.normalize_path(path)
            return {"ok": True, "target": target, "path": path, **endpoint.list_path(path)}

    def transfer_file(self, source, source_path, target, target_path, overwrite=False, timeout_sec=180):
        """source/target为目标ID，两路径为文件绝对路径；overwrite默认否，timeout_sec为总期限。"""
        if type(overwrite) is not bool or type(timeout_sec) is not int or not 10 <= timeout_sec <= 300:
            raise ValueError("overwrite须为布尔值，timeoutSec须为10至300秒整数")
        source, target = self.resolve_target(source), self.resolve_target(target)
        # 在任何联网前拒绝路径格式错误，不让错误参数触发SSH认证。
        source_path = FileEndpoint(remote=source != "local").normalize_path(source_path)
        target_path = FileEndpoint(remote=target != "local").normalize_path(target_path)
        if source == target and source_path == target_path:
            raise ValueError("源文件与目标文件不能相同")
        deadline = time.monotonic() + timeout_sec
        with ExitStack() as stack:
            src = self.connect_endpoint(stack, source, timeout_sec)
            dst = src if source == target else self.connect_endpoint(stack, target, self.remaining_time(deadline))
            info = src.stat_path(source_path)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("只传输普通文件，目录和符号链接请先明确整理")
            if not 0 <= info.st_size <= self.max_bytes:
                raise ValueError("文件超过当前256MiB单文件限制")
            try:
                dest_info = dst.stat_path(target_path)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    raise
            else:
                if not overwrite:
                    raise ValueError("目标文件已存在；默认不覆盖")
                if not stat.S_ISREG(dest_info.st_mode):
                    raise ValueError("不能覆盖目录、符号链接或特殊文件")
            temp = None
            try:
                temp, handle = dst.create_stage(target_path)
                with handle, src.open_file(source_path, "rb") as reader:
                    size, digest = self.copy_stream(reader, handle, deadline)
                    handle.flush()
                    if dst.sftp is None:
                        os.fsync(handle.fileno())
                if size != info.st_size:
                    raise ValueError("源文件在传输期间大小发生变化，未提交")
                current = src.stat_path(source_path)
                if current.st_size != info.st_size or current.st_mtime != info.st_mtime:
                    raise ValueError("源文件在传输期间发生变化，未提交")
                with dst.open_file(temp, "rb") as reader:
                    verified_size, verified_hash = self.copy_stream(reader, None, deadline)
                if size != verified_size or digest != verified_hash:
                    raise ValueError("目标内容SHA-256校验失败，未提交")
                self.remaining_time(deadline)
                dst.publish_file(temp, target_path, overwrite)
                temp = None
                return {"ok": True, "source": source, "sourcePath": source_path,
                    "target": target, "targetPath": target_path, "bytes": size,
                    "sha256": digest, "verified": True, "transport": "ssh-sftp",
                    "overwritten": bool(overwrite and 'dest_info' in locals())}
            finally:
                if temp is not None:
                    try:
                        dst.remove_stage(temp)
                    except OSError:
                        pass

    def remaining_time(self, deadline):
        """deadline为单调时钟期限；超时立即停止，避免无期限后台传输。"""
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("文件传输超过期限，未自动重试")
        return left

    def copy_stream(self, reader, writer, deadline):
        """reader/writer为读写句柄，writer可为空以仅验散列；每块检查期限和累计大小。"""
        size, digest = 0, hashlib.sha256()
        while True:
            self.remaining_time(deadline)
            part = reader.read(256 * 1024)
            if not part:
                return size, digest.hexdigest()
            size += len(part)
            if size > self.max_bytes:
                raise ValueError("文件增长超过单文件限制，未提交")
            digest.update(part)
            if writer is not None:
                written = writer.write(part)
                if written is not None and written != len(part):
                    raise OSError("文件写入不完整")
