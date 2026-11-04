"""终端核心协议测试：隔离签名、重传、实例互斥、执行幂等及通知可靠交接。

无注册参数；只用临时SQLite和回环端口，不调用模型、不执行外部命令、不发真实消息。
"""
import hashlib
import json
import sys
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from terminalConnection.client.CommandExecutor import CommandExecutor
from terminalConnection.client.TerminalAgent import TerminalAgent
from terminalConnection.client.TerminalJournal import TerminalJournal
from terminalConnection.completion.CompletionWatcher import CompletionWatcher
from terminalConnection.completion.TaskReport import TaskReport
from terminalConnection.security.DeviceIdentity import DeviceIdentity
from terminalConnection.server.TerminalManager import TerminalManager


class CoreProtocolTests(unittest.TestCase):
    """核对真实组件间的数据交接，不以模拟调用次数代替持久状态证据。"""

    def setUp(self):
        """无参数；创建一个服务器和两个具有独立身份的临时设备。"""
        self.temp = tempfile.TemporaryDirectory(prefix="synloveCoreTest")
        self.root = Path(self.temp.name)
        self.manager = TerminalManager({
            "runtimeDir": str(self.root / "server"),
            "keyPath": str(self.root / "server.pem"),
            "relayURL": "https://relay.example.com/v1", "relayToken": "test",
            "notificationInbox": str(self.root / "inbox"), "notificationTarget": "testOwner"})
        self.identities = {}
        for name in ("deviceOne", "deviceTwo"):
            identity = DeviceIdentity(self.root / (name + ".pem"))
            bundle = self.manager.pairing_bundle({"deviceId": name, "name": name})
            raw = self.manager.receive_message({"op": "pair", "pairCode": bundle["pairCode"],
                                                 "publicKey": identity.public_key()})
            self.assertTrue(DeviceIdentity.verify_envelope(bundle["serverPublicKey"], raw, name)["ok"])
            self.identities[name] = identity
        self.boots = {name: uuid.uuid4().hex for name in self.identities}

    def tearDown(self):
        """无参数；关闭测试数据库，移除本轮临时产物。"""
        self.manager.store.db.close()
        self.temp.cleanup()

    def heartbeat(self, device="deviceOne", **extra):
        """device为测试设备，extra覆盖心跳字段；经过真实双向签名后返回业务回执。"""
        body = {"op": "heartbeat", "bootId": self.boots[device], "platform": "Windows",
                "capabilities": ["device.probe", "shell.exec"], "events": [], "results": []}
        body.update(extra)
        envelope = self.identities[device].sign_envelope(device, body)
        reply = self.manager.receive_message(envelope)
        return DeviceIdentity.verify_envelope(self.manager.identity.public_key(), reply, device)

    def test_pair_receipt_can_recover_without_changing_identity(self):
        """无参数；同一次接入回执丢失后，相同公钥能恢复，其他公钥不能抢占。"""
        bundle = self.manager.pairing_bundle({"deviceId": "recoverDevice"})
        first = DeviceIdentity(self.root / "recover.pem")
        body = {"pairCode": bundle["pairCode"], "publicKey": first.public_key()}
        self.assertEqual(self.manager.store.pair_device(body), "recoverDevice")
        self.assertEqual(self.manager.store.pair_device(body), "recoverDevice")
        with self.assertRaises(ValueError):
            self.manager.store.pair_device({**body, "publicKey": self.identities["deviceOne"].public_key()})

    def test_tampered_and_wrong_device_signatures_are_rejected(self):
        """无参数；修改正文和设备归属都不能绕过验签。"""
        value = self.identities["deviceOne"].sign_envelope("deviceOne", {"op": "heartbeat"})
        value["body"] = value["body"][:-1] + ("A" if value["body"][-1]!="A" else "B")
        with self.assertRaises(Exception):
            self.manager.receive_message(value)
        value = self.identities["deviceTwo"].sign_envelope("deviceOne", {"op": "heartbeat"})
        with self.assertRaises(Exception):
            self.manager.receive_message(value)

    def test_stale_and_nonfinite_signed_timestamps_are_rejected(self):
        """无参数；即便签名有效，过期或非有限时间也不接受。"""
        for stamp in (time.time()-121,):
            with patch("terminalConnection.security.DeviceIdentity.time.time", return_value=stamp):
                value = self.identities["deviceOne"].sign_envelope("deviceOne", {"op": "heartbeat"})
            with self.assertRaises(ValueError):
                self.manager.receive_message(value)
        for stamp in (float("nan"),float("inf")):
            value=self.identities["deviceOne"].sign_envelope("deviceOne",{"op":"heartbeat"})
            value["time"]=stamp
            with self.assertRaises(ValueError):
                self.manager.receive_message(value)

    def test_base64_body_survives_javascript_number_normalization(self):
        """无参数；中转将整数浮点规范成整数后，正文中的0.0和任务时间仍能正确验签。"""
        body={"op":"heartbeat","bootId":self.boots["deviceOne"],
              "events":[{"id":"4"*64,"source":"codex","text":"跨语言测试","finishedAt":1700000000.0}],
              "results":[],"position":0.0}
        envelope=self.identities["deviceOne"].sign_envelope("deviceOne",body)
        transported=json.loads(json.dumps(envelope),
            parse_float=lambda value:int(float(value)) if float(value).is_integer() else float(value))
        verified=DeviceIdentity.verify_envelope(self.identities["deviceOne"].public_key(),transported,"deviceOne")
        self.assertEqual(verified,body)
        self.assertIsInstance(verified["position"],float)
        self.assertTrue(DeviceIdentity.verify_envelope(self.manager.identity.public_key(),
            self.manager.receive_message(transported),"deviceOne")["ok"])

    def test_legacy_envelope_remains_readable_during_upgrade(self):
        """无参数；仍在运行的旧终端可继续心跳，服务器按旧格式返回升级兼容回执。"""
        body={"op":"heartbeat","bootId":self.boots["deviceOne"]}
        envelope=self.identities["deviceOne"].sign_envelope("deviceOne",body,encoded=False)
        reply=self.manager.receive_message(envelope)
        self.assertNotIn("encoding",reply)
        self.assertIsInstance(reply["body"],dict)
        self.assertTrue(DeviceIdentity.verify_envelope(self.manager.identity.public_key(),reply,"deviceOne")["ok"])

    def test_relay_redispatch_returns_same_receipt_without_duplication(self):
        """无参数；同一VPS报文重派返回相同确认，仅保存一次事件。"""
        event = {"id": "a"*64, "source": "codex", "text": "测试任务结果"}
        body = {"op": "heartbeat", "bootId": self.boots["deviceOne"], "events": [event]}
        envelope = self.identities["deviceOne"].sign_envelope("deviceOne", body)
        first = self.manager.receive_message(envelope)
        second = self.manager.receive_message(envelope)
        public = self.manager.identity.public_key()
        self.assertEqual(DeviceIdentity.verify_envelope(public, first, "deviceOne"),
                         DeviceIdentity.verify_envelope(public, second, "deviceOne"))
        self.assertEqual(self.manager.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    def test_conflicting_boot_is_rejected_until_lease_expires(self):
        """无参数；不同实例不能同时占用同一设备身份，过期后可恢复接入。"""
        self.heartbeat()
        reply = self.heartbeat(bootId=uuid.uuid4().hex)
        self.assertTrue(reply["conflict"])
        with self.manager.store.db:
            self.manager.store.db.execute("UPDATE devices SET leaseUntil=0 WHERE id='deviceOne'")
        self.assertTrue(self.heartbeat(bootId=uuid.uuid4().hex)["ok"])

    def test_controlled_handover_requires_matching_previous_boot(self):
        """无参数；受控升级只能释放已核对的旧实例，不能误释放其他正在运行的实例。"""
        self.heartbeat()
        state=self.manager.store.instance_state("deviceOne")
        with self.assertRaises(ValueError):
            self.manager.store.release_instance("deviceOne",uuid.uuid4().hex)
        self.assertGreater(self.manager.store.instance_state("deviceOne")["leaseUntil"],time.time())
        self.manager.store.release_instance("deviceOne",state["bootId"])
        self.assertFalse(self.manager.store.list_devices()[0]["online"])
        self.assertTrue(self.heartbeat(bootId=uuid.uuid4().hex)["ok"])

    def test_generated_deadline_allows_probe_and_result_acknowledgement(self):
        """无参数；服务器生成有效期，终端真实执行探活，结果仅确认归属正确的指令。"""
        self.heartbeat()
        key = self.manager.store.queue_command("deviceOne", {"type": "probe"})
        reply = self.heartbeat()
        command = reply["commands"][0]
        self.assertGreater(command["expiresAt"], time.time())
        result = CommandExecutor().run_command(command)
        self.assertTrue(result["ok"])
        response = self.heartbeat(results=[{"id": key, "result": result},
                                           {"id": "b"*32, "result": {"ok": True}}])
        self.assertEqual(response["acceptedResults"], [key])
        self.assertEqual(self.manager.store.command_result(key)["result"], result)

    def test_command_identity_includes_target_and_does_not_refresh_deadline(self):
        """无参数；相同ID重查保持原指令，跨设备复用或改内容会拒绝。"""
        self.heartbeat()
        self.heartbeat("deviceTwo")
        body = {"id": "c"*32, "type": "probe"}
        self.manager.store.queue_command("deviceOne", body)
        first = self.heartbeat()["commands"][0]
        self.manager.store.queue_command("deviceOne", body)
        self.assertEqual(first, self.heartbeat()["commands"][0])
        for device, value in (("deviceTwo", body), ("deviceOne", {**body, "type": "cmd"})):
            with self.assertRaises(ValueError):
                self.manager.store.queue_command(device, value)

    def test_invalid_batch_rolls_back_heartbeat_and_prior_events(self):
        """无参数；批次后段无效时整批回滚，防止丢失未确认事件。"""
        event = {"id": "d"*64, "source": "codex", "text": "valid"}
        with self.assertRaises(ValueError):
            self.heartbeat(events=[event, {"id": "bad"}])
        self.assertEqual(self.manager.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        self.assertEqual(self.manager.store.get_device("deviceOne")["lastSeen"], 0)

    def test_long_chinese_completion_is_measured_as_utf8_bytes(self):
        """无参数；真实长度范围内的长中文摘要不会因JSON转义膨胀被拒绝。"""
        event={"id":"3"*64,"source":"codex","text":"测"*6000}
        reply=self.heartbeat(events=[event])
        self.assertEqual(reply["accepted"],[event["id"]])

    def test_invalid_local_event_never_poison_persistent_outbox(self):
        """无参数；本机入口拒绝错误标识和超大通知，防止心跳批次永久堵塞。"""
        journal=TerminalJournal(self.root/"invalidEvents.sqlite")
        try:
            for event in (
                {"id":"notValid","source":"codex","text":"test"},
                {"source":"unknown","text":"test"},
                {"source":"opencode","text":"测"*11000},
            ):
                with self.assertRaises(ValueError):
                    journal.add_event(event)
            self.assertEqual(journal.pending_items()[0],[])
        finally:
            journal.db.close()

    def test_large_outbox_is_split_without_losing_records(self):
        """无参数；大批中文结果与通知分批发送，Base64封装后仍在协议大小上限内。"""
        journal=TerminalJournal(self.root/"largeOutbox.sqlite")
        try:
            for index in range(20):
                key=f"{index:032x}"
                journal.claim_command({"id":key,"type":"probe"})
                journal.finish_command(key,{"ok":True,"stdout":"测"*30000})
                journal.add_event({"id":f"{index:064x}","source":"codex","text":"测"*6000})
            count=0
            while True:
                events,results=journal.pending_items()
                if not events and not results:
                    break
                envelope=self.identities["deviceOne"].sign_envelope("deviceOne",
                    {"events":events,"results":results})
                self.assertLess(len(json.dumps(envelope).encode("utf-8")),1024*1024)
                count+=len(events)+len(results)
                journal.acknowledge_items([row["id"] for row in events],[row["id"] for row in results])
            self.assertEqual(count,40)
        finally:
            journal.db.close()

    def test_concurrent_devices_preserve_isolated_deduplication(self):
        """无参数；并发双设备相同事件ID互不覆盖，同设备重传不产生重复通知。"""
        def send_batch(index):
            """index为批次编号；两个设备交替上报同一个稳定事件ID。"""
            return self.heartbeat("deviceOne" if index % 2 else "deviceTwo",
                events=[{"id": "e"*64, "source": "opencode", "text": "done"}])
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(send_batch, range(40)))
        self.assertTrue(all(row["ok"] for row in replies))
        self.assertEqual(self.manager.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)

    def test_restart_never_reexecutes_unknown_commands(self):
        """无参数；断电前已开始的命令恢复为结果未知，不能重新执行。"""
        path = self.root / "journal.sqlite"
        first = TerminalJournal(path)
        command = {"id": "f"*32, "type": "probe", "expiresAt": time.time()+120}
        self.assertTrue(first.claim_command(command))
        first.db.close()
        second = TerminalJournal(path)
        try:
            self.assertFalse(second.claim_command(command))
            self.assertTrue(second.pending_items()[1][0]["result"]["unknown"])
        finally:
            second.db.close()

    def test_new_boot_does_not_repeat_command_already_offered_to_old_process(self):
        """无参数；跨进程交接后结果未知的指令不重新派发，尚未交付的指令仍可执行。"""
        self.heartbeat()
        claimed=self.manager.store.queue_command("deviceOne",{"type":"probe"})
        self.assertEqual(self.heartbeat()["commands"][0]["id"],claimed)
        unclaimed=self.manager.store.queue_command("deviceOne",{"type":"probe"})
        with self.manager.store.db:
            self.manager.store.db.execute("UPDATE devices SET leaseUntil=0 WHERE id='deviceOne'")
        reply=self.heartbeat(bootId=uuid.uuid4().hex)
        self.assertEqual([row["id"] for row in reply["commands"]],[unclaimed])
        self.assertTrue(self.manager.store.command_result(claimed)["result"]["unknown"])

    def test_second_local_instance_cannot_modify_first_journal(self):
        """无参数；第二实例抢端口失败时不能恢复或改写第一实例的执行记录。"""
        config = {"runtimeDir": str(self.root / "client"), "keyPath": str(self.root / "client.pem"),
                  "relayURL": "https://relay.example.com/v1", "relayToken": "test",
                  "deviceId": "testDevice", "token": "test", "port": 0, "ffplay": "notInstalledTest"}
        first = TerminalAgent(config)
        try:
            first.journal.claim_command({"id": "1"*32, "type": "probe", "expiresAt": time.time()+120})
            with self.assertRaises(OSError):
                TerminalAgent({**config, "port": first.http.server_port})
            row = first.journal.db.execute("SELECT state FROM commands").fetchone()
            self.assertEqual(row["state"], "running")
        finally:
            first.http.server_close()
            first.journal.db.close()

    def test_server_notification_handoff_is_durable_and_idempotent(self):
        """无参数；持久事件交接现有收件箱，重复投递使用稳定文件ID。"""
        self.manager.store.update_device("deviceOne", {"settings": {"codex": True}})
        event = {"id": "2"*64, "source": "codex", "title": "隔离测试", "text": "无真实发送"}
        self.heartbeat(events=[event])
        self.manager.deliver_notifications()
        files = list((self.root / "inbox").glob("*.json"))
        self.assertEqual(len(files), 1)
        value = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertIn("无真实发送", value["text"])
        with self.manager.store.db:
            self.manager.store.db.execute("UPDATE messages SET delivered=0")
        self.manager.deliver_notifications()
        self.assertEqual(len(list((self.root / "inbox").glob("*.json"))), 1)

    def test_disabled_notifications_are_consumed_without_a_destination(self):
        """无参数；未配置收件人时也消耗关闭的来源，之后打开不能补发旧通知。"""
        target = self.manager.config.pop("notificationTarget")
        event = {"id": "3"*64, "source": "codex", "text": "关闭期间产生的旧结果"}
        self.heartbeat(events=[event])
        self.manager.deliver_notifications()
        row = self.manager.store.db.execute("SELECT delivered FROM messages").fetchone()
        self.assertEqual(row["delivered"], 1)
        self.manager.config["notificationTarget"] = target
        self.manager.store.update_device("deviceOne", {"settings": {"codex": True}})
        self.manager.deliver_notifications()
        self.assertFalse((self.root / "inbox").exists())

    def test_enabled_notifications_wait_for_a_destination(self):
        """无参数；已启用来源缺少收件人时保留事件，配置后交接一次且不提前建收件箱。"""
        self.manager.store.update_device("deviceOne", {"settings": {"opencode": True}})
        target = self.manager.config.pop("notificationTarget")
        self.heartbeat(events=[{"id": "4"*64, "source": "opencode", "text": "等待目的地"}])
        self.manager.deliver_notifications()
        row = self.manager.store.db.execute("SELECT delivered FROM messages").fetchone()
        self.assertEqual(row["delivered"], 0)
        self.assertFalse((self.root / "inbox").exists())
        self.manager.config["notificationTarget"] = target
        self.manager.deliver_notifications()
        self.assertEqual(len(list((self.root / "inbox").glob("*.json"))), 1)

    def test_completion_sources_baseline_enable_change_and_deduplicate(self):
        """无参数；首次不补发历史，新完成任务各记一条，反复扫描不重复。"""
        journal = TerminalJournal(self.root / "watcher.sqlite")
        watcher = CompletionWatcher(journal)
        reports = {name: TaskReport(name, name+"Task", "running", "testProject", "testTitle",
                                   "运行中", "", time.time()) for name in ("codex", "opencode")}
        watcher.sources = {name: SimpleNamespace(poll=lambda name=name:[reports[name]]) for name in reports}
        try:
            settings = {"codex": True, "opencode": True}
            watcher.scan_sources(settings)
            self.assertEqual(journal.pending_items()[0], [])
            for value in reports.values():
                value.status, value.fingerprint, value.result = "已完成", "done", "测试最终结果"
            watcher.scan_sources(settings)
            watcher.scan_sources(settings)
            events = journal.pending_items()[0]
            self.assertEqual({row["source"] for row in events}, {"codex", "opencode"})
            self.assertEqual(len(events), 2)
        finally:
            journal.db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
