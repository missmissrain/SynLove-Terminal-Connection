"""设备登记规范：区分设备类型、接入方式、父设备与当前实际支持的执行适配器。

本类无需注册参数；设备标识最长64字符，父设备链最多32层；ADB和嵌入式接入仅预留。
"""
import re
import uuid


class DeviceProfile:
    """集中维护登记约束，防止GUI、安装器与服务器分别猜测设备能力。"""

    KINDS = ("windows", "linux", "android", "embedded", "gateway")
    TRANSPORTS = ("direct", "adb", "gateway")
    DIRECT_KINDS = ("windows", "linux", "gateway")

    @classmethod
    def describe_types(cls):
        """无传入参数；返回设备类型及适配器支持范围，预留类型不会被描述为已实现。"""
        return {
            "kinds": list(cls.KINDS),
            "transports": [
                {"id": "direct", "implemented": True,
                 "kinds": list(cls.DIRECT_KINDS), "requiresParent": False},
                {"id": "adb", "implemented": False,
                 "kinds": ["android"], "requiresParent": True},
                {"id": "gateway", "implemented": False,
                 "kinds": ["android", "embedded"], "requiresParent": True},
            ],
            "capabilitiesFrom": "verifiedHeartbeat",
        }

    @classmethod
    def normalize_registration(cls, data):
        """data为登记对象；规范设备字段，不接收公钥、在线状态或用户声明的执行能力。"""
        if not isinstance(data, dict):
            raise ValueError("设备登记必须为对象")
        ident = data.get("deviceId") or "device" + uuid.uuid4().hex
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", ident):
            raise ValueError("设备标识格式错误")
        kind = data.get("kind", "windows")
        transport = data.get("transport", "direct")
        parent = data.get("parentId", "")
        if kind not in cls.KINDS or transport not in cls.TRANSPORTS:
            raise ValueError("设备类型或传输类型错误")
        if not isinstance(parent, str):
            raise ValueError("父设备标识必须为字符串")
        if parent and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", parent):
            raise ValueError("父设备标识格式错误")
        if transport == "direct" and parent:
            raise ValueError("直连设备不通过父设备转发")
        if transport != "direct" and not parent:
            raise ValueError("ADB或网关接入必须指定父设备")
        if transport == "adb" and kind != "android":
            raise ValueError("ADB接入仅适用于安卓设备")
        if transport == "gateway" and kind not in ("android", "embedded"):
            raise ValueError("网关接入仅适用于安卓或嵌入式子设备")
        name = data.get("name") or ident
        if not isinstance(name, str) or len(name) > 80:
            raise ValueError("设备名称必须为不超过80字符的字符串")
        return {"id": ident, "name": name, "kind": kind,
                "transport": transport, "parentId": parent}

    @staticmethod
    def validate_parent(db, device):
        """db为事务内连接，device为规范登记字段；检查父设备存在及关系无环。"""
        seen = {device["id"]}
        parent = device["parentId"]
        for _ in range(32):
            if not parent:
                return
            if parent in seen:
                raise ValueError("父设备关系不能形成循环")
            seen.add(parent)
            row = db.execute("SELECT parentId FROM devices WHERE id=?", (parent,)).fetchone()
            if not row:
                raise ValueError("父设备尚未登记")
            parent = row["parentId"] or ""
        raise ValueError("父设备层级过深")

    @classmethod
    def supports_direct_client(cls, kind, transport):
        """kind/transport为登记属性；仅返回当前Python终端客户端已实现的组合。"""
        return transport == "direct" and kind in cls.DIRECT_KINDS
