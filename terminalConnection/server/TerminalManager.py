"""终端管理业务：验签登记心跳、生成接入文件、持久接收通知并投递服务器现有QQ收件箱。

注册参数config含runtimeDir、keyPath、relay连接、adminToken；通知目标与路径只在服务器私有配置保存。
"""
import hashlib
import json
import os
import time
from pathlib import Path

from terminalConnection.security.DeviceIdentity import DeviceIdentity
from terminalConnection.server.DeviceStore import DeviceStore
from terminalConnection.completion.NoticeText import NoticeText


class TerminalManager:
    """管理设备注册与结果交接，不创建微信或QQ消息消费者。"""

    def __init__(self, config):
        """config为服务器私有配置；创建终端库及服务器签名身份，不读取其他设备私钥。"""
        self.config = config
        self.root = Path(config["runtimeDir"])
        self.root.mkdir(parents=True,exist_ok=True)
        self.identity = DeviceIdentity(config["keyPath"])
        self.store = DeviceStore(self.root/"devices.sqlite")

    def pairing_bundle(self, body):
        """body为管理员填写的设备信息；返回十分钟接入文件，内含公钥和一次性码。"""
        pair = self.store.create_pair(body)
        bundle = {**pair, "protocol":"synlove-terminal-v1",
            "serverPublicKey":self.identity.public_key(),
            **{key:self.config[key] for key in ("relayURL","relayToken")},
            "name":str(body.get("name") or pair["deviceId"]), "kind":body.get("kind","windows"),
            "transport":body.get("transport","direct"), "parentId":body.get("parentId","")}
        if self.config.get("tlsCaFile"):
            bundle["tlsCaPem"] = Path(self.config["tlsCaFile"]).read_text(encoding="utf-8")
        self.write_snapshot()
        return bundle

    def receive_message(self, envelope):
        """envelope为来自VPS的配对或签名心跳；验证后返回服务器签名确认。"""
        if envelope.get("op") == "pair":
            device = self.store.pair_device(envelope)
            return self.identity.sign_envelope(device, {"ok":True,"deviceId":device})
        device = envelope.get("deviceId")
        row = self.store.get_device(device)
        body = DeviceIdentity.verify_envelope(row["publicKey"], envelope, device)
        if body.get("op") != "heartbeat":
            raise ValueError("不支持的终端报文")
        reply = self.store.receive_heartbeat(device,body,envelope.get("nonce",""))
        self.write_snapshot()
        # 升级期间旧客户端仍可接收旧格式回执，新客户端统一使用不可变载荷。
        return self.identity.sign_envelope(device,reply,encoded=envelope.get("encoding")=="base64-json")

    def write_snapshot(self):
        """无参数；原子写出不含凭据的设备快照，供主程序卡片与设备管理读取。"""
        path = self.root/"devices.json"
        temp = path.with_suffix(".tmp")
        # 心跳和GUI修改可能并发写快照，统一复用数据库锁。
        with self.store.lock:
            rows = self.store.list_devices()
            temp.write_text(json.dumps({"devices":rows,"checkedAt":time.time()},ensure_ascii=False),encoding="utf-8")
            os.replace(temp,path)

    def deliver_notifications(self):
        """无参数；将终端持久通知写入服务器既有收件箱，去重ID稳定，不直接调用平台发送。"""
        target = self.config.get("notificationTarget")
        inbox_path = self.config.get("notificationInbox")
        if not target or not inbox_path:
            return
        inbox = Path(inbox_path)
        inbox.mkdir(parents=True,exist_ok=True)
        with self.store.lock:
            rows = self.store.db.execute("SELECT * FROM messages WHERE delivered=0 LIMIT 20").fetchall()
            for row in rows:
                event = json.loads(row["body"])
                device = self.store.db.execute("SELECT * FROM devices WHERE id=?",
                    (row["deviceId"],)).fetchone()
                source = event.get("source")
                if (not device or not device["enabled"] or source not in ("codex","opencode")
                    or not json.loads(device["settings"]).get(source,False)):
                    self.store.db.execute("UPDATE messages SET delivered=1 WHERE id=?", (row["id"],))
                    self.store.db.commit()
                    continue
                key = hashlib.sha256(("terminal:"+row["id"]).encode()).hexdigest()
                body = NoticeText.clean_text(event.get("text", ""))
                if not body:
                    self.store.db.execute("UPDATE messages SET delivered=1 WHERE id=?", (row["id"],))
                    self.store.db.commit()
                    continue
                text = "%s · %s\n%s\n%s" % (
                    device["name"],source,event.get("title","任务完成"),body)
                item = {"id":key,"text":text[:28000],"target":{"userOpenid":target}}
                final = inbox/(key+".json")
                temp = final.with_suffix(".tmp")
                with temp.open("w",encoding="utf-8") as file:
                    json.dump(item,file,ensure_ascii=False)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp,final)
                self.store.db.execute("UPDATE messages SET delivered=1 WHERE id=?", (row["id"],))
                self.store.db.commit()
