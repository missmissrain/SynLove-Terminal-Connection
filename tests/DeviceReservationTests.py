"""设备预留测试：隔离SQLite与本地临时HTTP接口，核对ADB、嵌入式登记及在线边界。

无注册参数；只使用临时目录和随机本机端口，不连接生产、VPS、模型或社交通道。
"""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from terminalConnection.security.DeviceIdentity import DeviceIdentity
from terminalConnection.server.DeviceProfile import DeviceProfile
from terminalConnection.server.DeviceStore import DeviceStore
from terminalConnection.server.TerminalAdminHandler import TerminalAdminHandler
from terminalConnection.server.TerminalManager import TerminalManager


class DeviceReservationTests(unittest.TestCase):
    """验证登记数据契约，防止预留设备被伪装成可执行设备。"""

    def setUp(self):
        """无传入参数；创建独立临时数据库和测试身份。"""
        self.temp = tempfile.TemporaryDirectory(prefix="synloveDeviceTest")
        self.root = Path(self.temp.name)
        self.store = DeviceStore(self.root / "devices.sqlite")
        self.identity = DeviceIdentity(self.root / "device.pem")

    def tearDown(self):
        """无传入参数；关闭测试数据库并回收此次测试的临时文件。"""
        self.store.db.close()
        self.temp.cleanup()

    def register_parent(self):
        """无传入参数；建立已配对但尚未发送心跳的测试网关。"""
        pair = self.store.create_pair({"deviceId": "gatewayOne", "name": "测试网关", "kind": "gateway"})
        self.store.pair_device({**pair, "publicKey": self.identity.public_key()})

    def test_auto_id_is_always_valid(self):
        """无传入参数；批量自动生成ID，验证不会随机生成数字开头的非法标识。"""
        for _ in range(64):
            device = self.store.reserve_device({"kind": "embedded"})
            self.assertRegex(device["id"], r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

    def test_android_adb_reserves_parent_without_fake_capabilities(self):
        """无传入参数；核对安卓ADB登记、父设备关联及不可伪造的在线状态。"""
        self.register_parent()
        self.store.reserve_device({
            "deviceId": "phoneOne", "name": "安卓手机", "kind": "android",
            "transport": "adb", "parentId": "gatewayOne",
            "online": True, "capabilities": ["shell.exec"], "publicKey": "ignored"})
        phone = next(row for row in self.store.list_devices() if row["id"] == "phoneOne")
        self.assertEqual(phone["parentId"], "gatewayOne")
        self.assertEqual(phone["status"], "reserved")
        self.assertFalse(phone["online"])
        self.assertFalse(phone["identityReady"])
        self.assertFalse(phone["adapterSupported"])
        self.assertEqual(phone["capabilities"], [])

    def test_embedded_can_reserve_gateway_relationship(self):
        """无传入参数；嵌入式设备可保存网关归属，后续无需更换设备ID。"""
        self.register_parent()
        device = self.store.reserve_device({
            "deviceId": "sensorOne", "kind": "embedded",
            "transport": "gateway", "parentId": "gatewayOne"})
        self.assertEqual(device["id"], "sensorOne")
        self.assertEqual(device["parentId"], "gatewayOne")

    def test_parent_is_required_for_indirect_transport(self):
        """无传入参数；ADB及网关方式不能登记无归属的子设备。"""
        for kind, transport in (("android", "adb"), ("embedded", "gateway")):
            with self.subTest(transport=transport), self.assertRaises(ValueError):
                self.store.reserve_device({"kind": kind, "transport": transport})

    def test_unknown_parent_is_rejected(self):
        """无传入参数；不存在的父设备不能作为后续路由依据。"""
        with self.assertRaises(ValueError):
            self.store.reserve_device({
                "deviceId": "phoneOne", "kind": "android",
                "transport": "adb", "parentId": "missingGateway"})
        self.assertEqual(self.store.list_devices(), [])

    def test_self_parent_and_cycles_are_rejected(self):
        """无传入参数；阻止自引用及重新登记形成的多设备循环。"""
        self.store.reserve_device({"deviceId": "sensorOne", "kind": "embedded"})
        self.store.reserve_device({"deviceId": "sensorTwo", "kind": "embedded",
                                   "transport": "gateway", "parentId": "sensorOne"})
        for parent in ("sensorOne", "sensorTwo"):
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                self.store.reserve_device({"deviceId": "sensorOne", "kind": "embedded",
                                           "transport": "gateway", "parentId": parent})
        row = next(row for row in self.store.list_devices() if row["id"] == "sensorOne")
        self.assertEqual(row["parentId"], "")

    def test_adb_cannot_be_used_for_windows(self):
        """无传入参数；不将Windows终端误登记成ADB设备。"""
        self.register_parent()
        with self.assertRaises(ValueError):
            self.store.reserve_device({"kind": "windows", "transport": "adb", "parentId": "gatewayOne"})

    def test_reserved_adapter_cannot_pair(self):
        """无传入参数；未实现的接入适配器不会下发可执行客户端接入码。"""
        self.register_parent()
        for data in (
            {"kind": "android"},
            {"kind": "embedded"},
            {"kind": "android", "transport": "adb", "parentId": "gatewayOne"},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.store.create_pair(data)
        self.assertEqual(len(self.store.list_devices()), 1)

    def test_reserved_adapter_rejects_direct_heartbeat(self):
        """无传入参数；预留设备不能靠直接提交心跳宣称自己已在线。"""
        self.store.reserve_device({"deviceId": "sensorOne", "kind": "embedded"})
        with self.assertRaises(ValueError):
            self.store.receive_heartbeat("sensorOne", {"bootId": "a"*32,
                "capabilities": ["device.probe"]}, "b"*32)
        self.assertFalse(self.store.list_devices()[0]["online"])

    def test_online_gateway_does_not_mark_child_online(self):
        """无传入参数；父设备可用不等于ADB连接上的手机已可用。"""
        self.register_parent()
        self.store.receive_heartbeat("gatewayOne", {"bootId": "a"*32,
            "capabilities": ["device.probe"]}, "b"*32)
        self.store.reserve_device({"deviceId": "phoneOne", "kind": "android",
            "transport": "adb", "parentId": "gatewayOne"})
        devices = {row["id"]: row for row in self.store.list_devices()}
        self.assertTrue(devices["gatewayOne"]["online"])
        self.assertFalse(devices["phoneOne"]["online"])

    def test_reserved_adapter_cannot_receive_commands(self):
        """无传入参数；即使旧数据意外留有心跳和能力，也不能调用未实现适配器。"""
        self.store.reserve_device({"deviceId": "sensorOne", "kind": "embedded"})
        with self.store.db:
            self.store.db.execute("UPDATE devices SET lastSeen=?,capabilities=? WHERE id=?",
                (time.time(), '["shell.exec"]', "sensorOne"))
        with self.assertRaises(ValueError):
            self.store.queue_command("sensorOne", {"type": "cmd", "params": {"command": "echo test"}})
        self.assertFalse(self.store.list_devices()[0]["online"])

    def test_paired_parent_cannot_be_overwritten(self):
        """无传入参数；补登记不能覆盖已配对设备公钥或改变其路由。"""
        self.register_parent()
        with self.assertRaises(ValueError):
            self.store.reserve_device({"deviceId": "gatewayOne", "kind": "embedded"})
        self.assertEqual(self.store.get_device("gatewayOne")["publicKey"], self.identity.public_key())

    def test_regenerating_pair_does_not_reset_notification_settings(self):
        """无参数；未配对设备重新领取接入文件时，已保存通知开关不会丢失。"""
        self.store.reserve_device({"deviceId":"futurePc","kind":"windows"})
        self.store.update_device("futurePc",{"settings":{"codex":True,"opencode":False}})
        self.store.create_pair({"deviceId":"futurePc","kind":"windows"})
        settings=self.store.list_devices()[0]["settings"]
        self.assertEqual(settings,{"codex":True,"opencode":False})

    def test_catalog_marks_adb_and_gateway_as_reserved(self):
        """无传入参数；UI获取能力目录时能区分已实现与预留协议。"""
        catalog = DeviceProfile.describe_types()
        rows = {row["id"]: row for row in catalog["transports"]}
        self.assertTrue(rows["direct"]["implemented"])
        self.assertFalse(rows["adb"]["implemented"])
        self.assertFalse(rows["gateway"]["implemented"])

    def test_admin_http_reservation_and_authentication(self):
        """无传入参数；通过真实本机HTTP核对认证、设备类型及预登记返回契约。"""
        manager = TerminalManager({"runtimeDir": str(self.root / "manager"),
                                   "keyPath": str(self.root / "manager.pem")})
        server = HTTPServer(("127.0.0.1", 0), TerminalAdminHandler)
        server.manager = manager
        server.token = "isolated-test-token"
        server.node = SimpleNamespace(connected=False)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(base + "/v1/deviceTypes", timeout=3)
            self.assertEqual(error.exception.code, 401)
            headers = {"Authorization": "Bearer isolated-test-token", "Content-Type": "application/json"}
            request = urllib.request.Request(base + "/v1/deviceTypes", headers=headers)
            with opener.open(request, timeout=3) as response:
                self.assertIn("android", json.load(response)["kinds"])
            body = {"deviceId": "futureBoard", "kind": "embedded", "name": "预留开发板"}
            request = urllib.request.Request(base + "/v1/device/reserve",
                data=json.dumps(body).encode(), headers=headers)
            with opener.open(request, timeout=3) as response:
                self.assertEqual(json.load(response)["device"]["id"], "futureBoard")
            snapshot = json.loads((self.root / "manager/devices.json").read_text(encoding="utf-8"))
            self.assertEqual(snapshot["devices"][0]["status"], "reserved")
            self.assertNotIn("publicKey", snapshot["devices"][0])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
            manager.store.db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
