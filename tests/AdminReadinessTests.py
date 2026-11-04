"""管理接口就绪性测试：使用临时服务器配置核对通知目的地状态，不回传收件人或管理密钥。

无注册参数；仅监听随机回环端口，不连接VPS，不发送社交消息。
"""
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace
from terminalConnection.server.TerminalAdminHandler import TerminalAdminHandler
from terminalConnection.server.TerminalManager import TerminalManager


class AdminReadinessTests(unittest.TestCase):
    """通过真实HTTP请求确认连接可用和通知可发送是两个独立状态。"""

    def test_health_requires_both_notification_fields_and_hides_private_values(self):
        """无参数；只有目的地与收件箱均存在才就绪，响应不得携带私有字段值。"""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manager = TerminalManager({"runtimeDir": str(root / "runtime"),
                                       "keyPath": str(root / "server.pem")})
            server = HTTPServer(("127.0.0.1", 0), TerminalAdminHandler)
            server.manager = manager
            server.node = SimpleNamespace(connected=True, last_error="")
            server.token = "isolatedAdminSecret"
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for fields, expected in (({}, False), ({"notificationTarget": "isolatedOwner"}, False),
                                         ({"notificationTarget": "isolatedOwner",
                                           "notificationInbox": str(root / "inbox")}, True)):
                    manager.config.update(fields)
                    request = urllib.request.Request(
                        "http://127.0.0.1:%d/v1/health" % server.server_port,
                        headers={"Authorization": "Bearer isolatedAdminSecret"})
                    with urllib.request.urlopen(request, timeout=3) as response:
                        text = response.read().decode("utf-8")
                    value = json.loads(text)
                    self.assertTrue(value["relayConnected"])
                    self.assertEqual(value["notificationsConfigured"], expected)
                    self.assertNotIn("isolatedOwner", text)
                    self.assertNotIn("isolatedAdminSecret", text)
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()
                manager.store.db.close()
