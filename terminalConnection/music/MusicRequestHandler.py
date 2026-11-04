"""音乐接收协议：只监听回环地址，通过本机随机令牌鉴权并限制请求大小。

注册参数由 MusicServer 设置：player、token、max_body；默认最大请求65536字节。
该接口仅供已通过SSH认证的本机命令客户端使用，不开放公网。
"""

import hmac
import json
from http.server import BaseHTTPRequestHandler


class MusicRequestHandler(BaseHTTPRequestHandler):
    """处理有界 JSON 音乐指令，拒绝无令牌请求且不记录带签名的URL。"""

    def do_POST(self):
        """无方法参数；验证 Bearer、协议路径及正文，再调用唯一播放器实例。"""
        if self.path != "/v1/music":
            self.send_json(404, {"ok": False, "error": "未知接口"})
            return
        expected = "Bearer " + self.server.token
        if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            self.send_json(401, {"ok": False, "error": "认证失败"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= self.server.max_body or self.headers.get("Transfer-Encoding"):
                self.send_json(413, {"ok": False, "error": "请求大小无效"})
                return
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("指令必须是JSON对象")
            self.send_json(200, self.server.player.execute_request(data))
        except (ValueError, TypeError, UnicodeError):
            self.send_json(400, {"ok": False, "error": "指令或音频地址无效"})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": "播放器执行失败：" + type(exc).__name__})

    def send_json(self, status, data):
        """status 为HTTP状态码，data 为可序列化安全结果；禁止浏览器缓存。"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def setup(self):
        """无方法参数；设置读取超时，避免不完整请求无限占据唯一服务线程。"""
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, fmt, *args):
        """fmt、args 为HTTP默认日志；静默，避免写入凭据、URL及个人曲目信息。"""
        return
