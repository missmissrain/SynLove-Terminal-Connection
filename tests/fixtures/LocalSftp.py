"""隔离SFTP替身：把远端绝对路径映射到临时目录，不接触真实设备或凭据。

注册参数root为测试目录，corrupt默认为False；启用后故意损坏暂存文件读回数据。
"""
import io
import os
from pathlib import Path
from types import SimpleNamespace


class LocalSftp:
    """实现文件端点所需的SFTP子集，验证校验、原子提交与失败清理逻辑。"""

    def __init__(self, root, corrupt=False):
        """root为隔离根目录，corrupt为读回校验故障注入。"""
        self.root, self.corrupt = Path(root), corrupt

    def resolve_path(self, path):
        """path为远端绝对路径；确保测试数据始终限制在临时目录内。"""
        value = (self.root / path.lstrip("/")).resolve()
        value.relative_to(self.root.resolve())
        return value

    def lstat(self, path):
        """path为测试路径；返回不跟随最终链接的原始文件属性。"""
        return self.resolve_path(path).lstat()

    def open(self, path, mode):
        """path/mode为测试路径及模式；仅模拟暂存文件校验错误，不改变源文件。"""
        value = self.resolve_path(path)
        if self.corrupt and mode == "rb" and str(value).endswith(".part"):
            data = value.read_bytes()
            return io.BytesIO(b"!" + data[1:] if data else b"!")
        return open(value, "xb" if mode == "wx" else mode)

    def rename(self, source, target):
        """source/target为暂存与目标；原子禁止覆盖，模拟SFTP标准rename。"""
        os.link(self.resolve_path(source), self.resolve_path(target))
        os.unlink(self.resolve_path(source))

    def posix_rename(self, source, target):
        """source/target为暂存与目标；模拟OpenSSH的原子覆盖扩展。"""
        os.replace(self.resolve_path(source), self.resolve_path(target))

    def remove(self, path):
        """path为本轮暂存文件；只删除映射到隔离目录的指定文件。"""
        self.resolve_path(path).unlink()

    def listdir_iter(self, path, read_aheads=1):
        """path为目录；以生成器模拟SFTP目录属性迭代。"""
        for item in self.resolve_path(path).iterdir():
            info = item.lstat()
            yield SimpleNamespace(filename=item.name, st_mode=info.st_mode, st_size=info.st_size)
