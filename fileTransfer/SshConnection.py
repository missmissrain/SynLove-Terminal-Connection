"""受信SSH会话：复用远控目标配置和主机指纹策略，为SFTP提供已认证连接。

注册参数config为RemoteTargetConfig，target为已配置别名；默认禁用agent与自动寻钥。
"""
import paramiko
from remoteControl.PinnedHostPolicy import PinnedHostPolicy


class SshConnection:
    """管理单次SSH与SFTP生命周期，不接受模型传入主机或凭据。"""

    def __init__(self, config, target, timeout=20, client_factory=None):
        """config/target为本机配置及目标；timeout为SFTP单次读写期限，factory用于隔离测试。"""
        self.config, self.target, self.timeout = config, target, timeout
        self.factory = client_factory or paramiko.SSHClient
        self.client = None
        self.sftp = None

    def __enter__(self):
        """无参数；执行与CMD相同的认证和指纹验证，再建立SFTP子系统。"""
        cfg = self.config.resolve_target(self.target)
        self.client = self.factory()
        try:
            if cfg["known"]:
                self.client.load_host_keys(cfg["known"])
            self.client.set_missing_host_key_policy(
                PinnedHostPolicy(cfg["fingerprint"]) if cfg["fingerprint"] else paramiko.RejectPolicy())
            self.client.connect(hostname=cfg["host"], port=cfg["port"], username=cfg["user"],
                password=cfg["password"] or None, key_filename=cfg["key"] or None,
                passphrase=cfg["passphrase"], timeout=cfg["timeout"],
                auth_timeout=cfg["timeout"], banner_timeout=cfg["timeout"],
                allow_agent=False, look_for_keys=False)
            transport = self.client.get_transport()
            if transport is None or not transport.is_active():
                raise paramiko.SSHException("SSH连接未就绪")
            if cfg["fingerprint"]:
                PinnedHostPolicy(cfg["fingerprint"]).missing_host_key(
                    self.client, cfg["host"], transport.get_remote_server_key())
            self.sftp = self.client.open_sftp()
            self.sftp.get_channel().settimeout(self.timeout)
            return self.sftp
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, kind, error, tb):
        """kind/error/tb为上下文异常；始终关闭SFTP和SSH，不掩盖业务异常。"""
        try:
            if self.sftp is not None:
                self.sftp.close()
        finally:
            if self.client is not None:
                self.client.close()
