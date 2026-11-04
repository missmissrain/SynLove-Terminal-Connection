"""设备身份：生成本机Ed25519密钥并签名JSON信封，私钥不发送到服务器。

注册参数key_path为私钥文件；默认签名时间窗120秒，公钥为原始32字节的Base64。
"""
import base64
import json
import math
import time
import uuid
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


class DeviceIdentity:
    """提供设备签名、服务器响应验证及首次本机密钥创建。"""

    def __init__(self, key_path):
        """key_path为已受ACL保护的本机PEM文件；不存在时只在本机新建。"""
        self.path = Path(key_path)
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            key = Ed25519PrivateKey.generate()
            with self.path.open("xb") as file:
                file.write(key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.key = serialization.load_pem_private_key(self.path.read_bytes(), password=None)

    def public_key(self):
        """无参数；返回可回传的公钥，不返回私钥内容或路径。"""
        return base64.b64encode(self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()

    @staticmethod
    def canonical_json(data):
        """data为JSON对象；输出固定排序与UTF8字节，使跨平台签名一致。"""
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def sign_envelope(self, device, body, encoded=True):
        """device/body为归属及正文；默认Base64封装正文，避免中转改写浮点数使签名失效。"""
        if not isinstance(body,dict):
            raise ValueError("设备正文必须为对象")
        payload=body
        if encoded:
            payload=base64.b64encode(json.dumps(body,sort_keys=True,separators=(",",":"),
                ensure_ascii=False,allow_nan=False).encode("utf-8")).decode("ascii")
        item = {"deviceId": device, "time": int(time.time()), "nonce": uuid.uuid4().hex, "body": payload}
        if encoded:
            item["encoding"]="base64-json"
        item["signature"] = base64.b64encode(self.key.sign(self.canonical_json(item))).decode()
        return item

    @staticmethod
    def verify_envelope(public, item, device=None):
        """public为可信公钥，item为信封；验证归属、签名和时效后返回业务正文。"""
        if not isinstance(item, dict) or (device and item.get("deviceId") != device):
            raise ValueError("设备身份不匹配")
        data = {key: value for key, value in item.items() if key != "signature"}
        stamp=float(data.get("time",0))
        if not math.isfinite(stamp) or abs(time.time() - stamp) > 120:
            raise ValueError("设备时间窗失效")
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public, validate=True)).verify(
            base64.b64decode(item["signature"], validate=True), DeviceIdentity.canonical_json(data))
        body=data.get("body")
        if data.get("encoding")=="base64-json":
            if not isinstance(body,str) or len(body)>1024*1024:
                raise ValueError("设备签名载荷大小无效")
            body=json.loads(base64.b64decode(body,validate=True).decode("utf-8"))
        elif data.get("encoding") is not None:
            raise ValueError("设备签名编码不支持")
        if not isinstance(body,dict):
            raise ValueError("设备报文正文必须为对象")
        return body
