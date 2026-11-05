"""文件端点：统一处理当前机器与受信SFTP端的路径、读取、暂存及原子发布。

注册参数sftp为空表示MCP所在机器；路径必须绝对，远端Windows路径统一成C:/形式。
默认不创建目标父目录，不覆盖已有文件，不传目录或符号链接。
"""
import errno
import os
import re
import stat
import uuid
from pathlib import Path


class FileEndpoint:
    """包装文件操作，保持传输层不经过shell、不解析命令文本。"""

    def __init__(self, sftp=None, remote=False):
        """sftp为可选已认证SFTPClient；remote仅供联网前校验远端路径，缺省操作当前机器。"""
        self.sftp = sftp
        self.remote = remote or sftp is not None

    def normalize_path(self, path):
        """path为用户明确指定的绝对文件路径；拒绝空字符、相对路径及Windows设备/网络路径。"""
        if not isinstance(path, str) or not path or len(path) > 4096 or "\0" in path:
            raise ValueError("文件路径必须为非空字符串，最多4096字符")
        if not self.remote:
            value = Path(path).expanduser()
            if not value.is_absolute() or path.startswith(("\\\\", "//")):
                raise ValueError("请提供本机绝对路径，不支持网络共享路径")
            return str(value)
        value = path.replace("\\", "/")
        if value.startswith("//") or not (value.startswith("/") or re.match(r"^[A-Za-z]:/", value)):
            raise ValueError("请提供远端绝对路径，如C:/目录/文件或/home/user/file")
        if re.match(r"^/?[A-Za-z]:/", value):
            tail = value.lstrip("/")[2:]
            if ":" in tail:
                raise ValueError("不支持Windows备用数据流路径")
        return value

    def stat_path(self, path):
        """path为规范路径；读取最终节点自身属性，保留符号链接识别能力。"""
        return self.sftp.lstat(path) if self.sftp else os.lstat(path)

    def open_file(self, path, mode):
        """path/mode为规范路径和二进制模式；创建暂存文件时强制独占。"""
        return self.sftp.open(path, mode) if self.sftp else open(path, mode)

    def list_path(self, path, limit=200):
        """path为规范路径，limit为最多返回条目；返回名称、大小、类型，不读取文件正文。"""
        if not stat.S_ISDIR(self.stat_path(path).st_mode):
            raise ValueError("目录浏览路径必须为真实目录")
        entries = []
        if self.sftp:
            iterator = self.sftp.listdir_iter(path, read_aheads=1)
            try:
                for item in iterator:
                    entries.append(self.describe_entry(item.filename, item))
                    if len(entries) > limit:
                        break
            finally:
                iterator.close()
        else:
            with os.scandir(path) as iterator:
                for item in iterator:
                    entries.append(self.describe_entry(item.name, item.stat(follow_symlinks=False)))
                    if len(entries) > limit:
                        break
        return {"entries": entries[:limit], "truncated": len(entries) > limit}

    def describe_entry(self, name, info):
        """name/info为目录项名称及属性；区分链接、目录和普通文件。"""
        kind = ("link" if stat.S_ISLNK(info.st_mode) else
                "directory" if stat.S_ISDIR(info.st_mode) else
                "file" if stat.S_ISREG(info.st_mode) else "other")
        return {"name": name, "type": kind, "bytes": info.st_size}

    def create_stage(self, path):
        """path为最终目标路径；在同目录独占创建随机暂存文件，返回路径与写句柄。"""
        temp = path + ".synlove-" + uuid.uuid4().hex + ".part"
        return temp, self.open_file(temp, "wx" if self.sftp else "xb")

    def publish_file(self, temp, path, overwrite):
        """temp/path为同目录暂存和目标；校验通过后原子发布，默认禁止覆盖。"""
        if overwrite:
            try:
                info = self.stat_path(path)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    raise
            else:
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("拒绝覆盖目录、链接或特殊文件")
            if self.sftp:
                # 服务端不支持原子覆盖则失败，不能用删除旧文件作为降级。
                self.sftp.posix_rename(temp, path)
            else:
                os.replace(temp, path)
        elif self.sftp:
            # SFTP标准rename禁止覆盖现有目标。
            self.sftp.rename(temp, path)
        else:
            os.link(temp, path)
            os.unlink(temp)

    def remove_stage(self, temp):
        """temp为本次随机暂存路径；失败时仅清除此文件，不能删除用户目标。"""
        if self.sftp:
            self.sftp.remove(temp)
        else:
            os.unlink(temp)
