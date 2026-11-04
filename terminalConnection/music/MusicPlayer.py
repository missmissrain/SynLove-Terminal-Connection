"""本机音乐播放器：仅管理自己创建的 ffplay，通过进程句柄控制暂停、继续和停止。

注册参数：config 为本地配置；ffplay 为可执行文件，volume 默认35，allowedHosts为允许的音频域名。
不保存签名地址、不调用系统媒体键、不接触其他播放器；状态由此实例统一维护。
"""

import ctypes
import hashlib
import json
import ipaddress
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from urllib.parse import urlsplit


class MusicPlayer:
    """在交互用户会话内运行播放器，并提供可验证的播放进度。"""

    def __init__(self, config):
        """config 为播放器配置；初始化唯一进程、锁和有界幂等记录。"""
        self.config = config
        self.lock = threading.RLock()
        self.proc = None
        self.state = {"playing": False, "paused": False, "playbackId": "", "position": 0.0}
        self.seen = {}
        self.ffplay = shutil.which(config.get("ffplay", "ffplay"))
        if not self.ffplay:
            raise ValueError("ffplay 不存在，请配置其完整路径")

    def validate_url(self, url):
        """url 为服务器解析的音频地址；只接受允许域名的公网 HTTP/HTTPS 音频。"""
        if not isinstance(url, str) or len(url) > 16000 or any(ord(c) < 32 for c in url):
            raise ValueError("音频地址无效")
        item = urlsplit(url)
        host = (item.hostname or "").lower()
        allowed = self.config.get("allowedHosts") or ["kugou.com", "kugou.net", "kgimg.com"]
        if item.scheme not in ("http", "https") or item.username or item.password:
            raise ValueError("仅允许 HTTP/HTTPS 音频地址")
        if not any(host == suffix or host.endswith("." + suffix) for suffix in allowed):
            raise ValueError("音频域名未授权")
        addresses = socket.getaddrinfo(host, item.port or (443 if item.scheme == "https" else 80))
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError("音频域名必须解析到公网地址")

    def execute_request(self, data):
        """data 为带 action/requestId 的指令；串行执行，重复请求仅返回当前状态，不重复播放。"""
        action = data.get("action")
        if action == "status":
            return self.get_status()
        if action not in ("play", "pause", "resume", "stop"):
            raise ValueError("不支持的音乐操作")
        key = data.get("requestId")
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError("写操作必须提供 requestId")
        with self.lock:
            digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()
            if key in self.seen:
                if self.seen[key][1] != digest:
                    return {"ok": False, "error": "requestId不能用于不同指令", "conflict": True}
                return {**self.get_status(), "duplicate": True,
                        "acceptedPlaybackId": self.seen[key][0]}
            expected = data.get("expectedPlaybackId")
            if expected and expected != self.state["playbackId"]:
                return {"ok": False, "error": "播放已被其他请求替换", "conflict": True}
            if action == "play":
                self.start_playback(data)
            elif action == "stop":
                self.stop_playback()
            elif action in ("pause", "resume"):
                self.set_paused(action == "pause")
            self.seen[key] = (self.state["playbackId"], digest)
            if len(self.seen) > 256:
                self.seen.pop(next(iter(self.seen)))
            return self.get_status()

    def start_playback(self, data):
        """data 包含 url、可选 song 和 volume；验证通过后替换自己持有的播放器。"""
        self.validate_url(data.get("url"))
        volume = data.get("volume", self.config.get("volume", 35))
        if type(volume) is not int or not 0 <= volume <= 100:
            raise ValueError("volume 必须是0至100整数")
        self.stop_playback()
        args = [self.ffplay, "-nodisp", "-autoexit", "-loglevel", "info", "-stats",
                "-protocol_whitelist", "http,https,tcp,tls,crypto",
                "-volume", str(volume), data["url"]]
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, creationflags=0x08000000)
        song = data.get("song") if isinstance(data.get("song"), dict) else {}
        self.state = {"playing": True, "paused": False, "playbackId": uuid.uuid4().hex,
                      "position": 0.0, "pid": self.proc.pid, "volume": volume,
                      "song": {name: str(song.get(name, ""))[:160] for name in ("name", "artist")},
                      "startedAt": time.time(), "exitCode": None}
        threading.Thread(target=self.read_progress, args=(self.proc,), daemon=True).start()

    def read_progress(self, proc):
        """proc 为本实例播放器；解析 ffplay 时钟，持续排空 stderr，避免管道阻塞。"""
        buf = b""
        while True:
            byte = proc.stderr.read(1)
            if not byte:
                break
            if byte in (b"\r", b"\n"):
                match = re.match(rb"\s*(\d+(?:\.\d+)?)\s+(?:M-A|A-V|M-V)", buf)
                if match:
                    with self.lock:
                        if self.proc is proc:
                            self.state["position"] = float(match.group(1))
                buf = b""
            else:
                buf = (buf + byte)[-2048:]
        proc.stderr.close()
        code = proc.wait()
        with self.lock:
            if self.proc is proc:
                self.state.update(playing=False, paused=False, exitCode=code)

    def set_paused(self, paused):
        """paused 为目标暂停状态；重复暂停或继续不会反向切换。"""
        if not self.proc or self.proc.poll() is not None:
            raise ValueError("当前没有活动播放器")
        if self.state["paused"] == paused:
            return
        # 直接使用 Popen 持有的进程句柄，避免按 PID 操作误伤后来复用该 PID 的进程。
        call = ctypes.windll.ntdll.NtSuspendProcess if paused else ctypes.windll.ntdll.NtResumeProcess
        call.argtypes = [ctypes.c_void_p]
        call.restype = ctypes.c_long
        if call(int(self.proc._handle)) != 0:
            raise RuntimeError("播放器暂停或继续失败")
        self.state["paused"] = paused

    def stop_playback(self):
        """无参数；停止且回收本实例播放器，不通过 taskkill 扫描或删除其他进程。"""
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.state.update(playing=False, paused=False, stopped=True)

    def get_status(self):
        """无参数；返回进程存活、音频时钟及目标会话信息，不返回音频签名地址。"""
        with self.lock:
            alive = self.proc is not None and self.proc.poll() is None
            if self.proc is not None and not alive:
                self.state["exitCode"] = self.proc.returncode
                self.state["paused"] = False
            return {"ok": True, **self.state, "playing": alive}
