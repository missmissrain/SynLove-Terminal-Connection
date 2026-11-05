"""文件传输隔离测试：双向/跨端二进制、中文路径、覆盖竞争、校验损坏和协议参数。

默认只使用临时目录与SFTP替身，不连接公网、不读取正式token，不安装依赖。
"""
import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

from fileTransfer.FileTransferService import FileTransferService
from fileTransfer.FileTransferTools import FileTransferTools
from tests.fixtures.LocalSftp import LocalSftp


class FileTransferTests(unittest.TestCase):
    """验证文件完整性与失败边界，不能用通过此测试代替实际SSH验收。"""

    def setUp(self):
        """无参数；创建独立的当前端、终端甲、终端乙目录及固定配置替身。"""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remotes = {}
        for name in ("alpha", "beta"):
            folder = self.root / name
            folder.mkdir()
            self.remotes[name] = LocalSftp(folder)
        self.config = Mock()
        self.config.read_config.return_value = {"targets": {
            "alpha": {"enabled": True, "deviceId": "laptop"},
            "beta": {"enabled": True}}}

        @contextmanager
        def connect_target(config, target, timeout):
            """参数为配置、固定目标和读写期限；只返回隔离SFTP替身。"""
            yield self.remotes[target]
        self.service = FileTransferService(self.config, connect_target)
        self.source = self.root / "中文 源文件.bin"
        self.data = bytes(range(256)) * 2000 + "欣爱文件双向测试".encode()
        self.source.write_bytes(self.data)

    def test_bidirectional_and_peer_transfer(self):
        """无参数；验证当前端上传、终端间转发与下载回当前端的字节完全一致。"""
        result = self.service.transfer_file("local", str(self.source), "laptop", "/上传 中文.bin")
        self.assertTrue(result["verified"])
        self.assertEqual(result["sha256"], hashlib.sha256(self.data).hexdigest())
        self.service.transfer_file("alpha", "/上传 中文.bin", "beta", "/转发.bin")
        output = self.root / "下载.bin"
        self.service.transfer_file("beta", "/转发.bin", "local", str(output))
        self.assertEqual(output.read_bytes(), self.data)
        self.assertEqual(self.service.list_files("alpha", "/")["entries"][0]["name"], "上传 中文.bin")
        self.assertFalse(list(self.root.rglob("*.part")))

    def test_overwrite_and_failed_verification_preserve_destination(self):
        """无参数；默认不覆盖，显式覆盖可用，损坏读回数据时原文件和暂存清理正确。"""
        path = self.root / "alpha" / "existing.bin"
        path.write_bytes(b"old")
        with self.assertRaises(ValueError):
            self.service.transfer_file("local", str(self.source), "alpha", "/existing.bin")
        self.assertEqual(path.read_bytes(), b"old")
        self.remotes["alpha"].corrupt = True
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.service.transfer_file("local", str(self.source), "alpha", "/existing.bin", True)
        self.assertEqual(path.read_bytes(), b"old")
        self.assertFalse(list(self.root.rglob("*.part")))
        self.remotes["alpha"].corrupt = False
        self.assertTrue(self.service.transfer_file(
            "local", str(self.source), "alpha", "/existing.bin", True)["overwritten"])
        self.assertEqual(path.read_bytes(), self.data)

    def test_invalid_inputs_limits_and_empty_file(self):
        """无参数；路径、目标、参数类型与大小上限正确拒绝，空文件可以完整传输。"""
        for args in [
            ("unknown", "/source", "local", str(self.root / "bad")),
            ("local", "relative.bin", "alpha", "/bad"),
            ("local", str(self.source), "alpha", "../bad"),
            ("local", str(self.source), "alpha", "/bad", True, True),
            ("local", str(self.source), "local", str(self.source))]:
            with self.assertRaises(ValueError):
                self.service.transfer_file(*args)
        self.service.max_bytes = 5
        with self.assertRaises(ValueError):
            self.service.transfer_file("local", str(self.source), "alpha", "/large.bin")
        self.source.write_bytes(b"")
        result = self.service.transfer_file("local", str(self.source), "alpha", "/empty.bin")
        self.assertEqual(result["bytes"], 0)
        self.assertEqual(result["sha256"], hashlib.sha256(b"").hexdigest())

    def test_concurrent_no_overwrite(self):
        """无参数；并发上传同一目标仅一个成功，其余不能覆盖获胜文件。"""
        target = self.root / "winner.bin"

        def run_transfer(index):
            """index为并发编号；预期失败只允许文件已存在类错误。"""
            try:
                self.service.transfer_file("local", str(self.source), "local", str(target))
                return True
            except (ValueError, FileExistsError):
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run_transfer, range(8)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(target.read_bytes(), self.data)
        self.assertFalse(list(self.root.rglob("*.part")))

    def test_tool_contract_rejects_extra_credentials(self):
        """无参数；工具不能注入任意主机或凭据，列表也不泄露配置字段。"""
        tools = FileTransferTools(self.service)
        with self.assertRaises(ValueError):
            tools.call_tool("remote_file_targets", {"host": "injected"})
        listed = tools.call_tool("remote_file_targets", {})["fileTargets"]
        self.assertEqual(listed[0]["target"], "local")
        self.assertTrue(all("host" not in item and "password" not in item for item in listed))


if __name__ == "__main__":
    unittest.main()
