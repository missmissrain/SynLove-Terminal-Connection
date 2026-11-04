"""终端常驻客户端：心跳、命令日志、音乐和编码完成通知统一归属本进程。

注册参数config含deviceId、keyPath、serverPublicKey、relay与本机token/port；默认心跳3秒。
"""
import json
import os
import platform
import queue
import socket
import threading
import time
import uuid
from http.server import HTTPServer
from pathlib import Path
from terminalConnection.client.CommandExecutor import CommandExecutor
from terminalConnection.client.TerminalJournal import TerminalJournal
from terminalConnection.client.TerminalLocalHandler import TerminalLocalHandler
from terminalConnection.completion.CompletionWatcher import CompletionWatcher
from terminalConnection.music.MusicPlayer import MusicPlayer
from terminalConnection.security.DeviceIdentity import DeviceIdentity
from terminalConnection.transport.RelayTransport import RelayTransport


class TerminalAgent:
    """每台机器一个实例；服务器租约同时拒绝其他机器复用同一设备身份。"""

    def __init__(self,config):
        """config为终端私有配置，复用已安装依赖；占端口失败即退出，不抢已有实例。"""
        self.config=config
        root=Path(config["runtimeDir"])
        root.mkdir(parents=True,exist_ok=True)
        self.identity=DeviceIdentity(config["keyPath"])
        self.transport=RelayTransport(config)
        self.settings={}
        self.boot=uuid.uuid4().hex
        self.commands=queue.Queue(maxsize=32)
        self.running=set()
        self.command_lock=threading.Lock()
        self.stopped=threading.Event()
        try:
            self.player=MusicPlayer(config)
        except ValueError:
            self.player=None
        self.executor=CommandExecutor(self.player,config.get("shellEnabled",False))
        self.http=HTTPServer(("127.0.0.1",int(config.get("port",17863))),TerminalLocalHandler,bind_and_activate=False)
        self.http.allow_reuse_address=False
        if hasattr(socket,"SO_EXCLUSIVEADDRUSE"):
            self.http.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try:
            self.http.server_bind()
            self.http.server_activate()
        except Exception:
            self.http.server_close()
            raise
        # 端口独占成功后才恢复日志，防止第二实例改动正在执行的命令状态。
        self.journal=TerminalJournal(root/"terminal.sqlite")
        self.watcher=CompletionWatcher(self.journal)
        self.http.player=self.player
        self.http.token=config["token"]
        self.http.max_body=65536
        self.http.agent=self

    def capabilities(self):
        """无参数；按实际依赖与安装授权返回能力，不能将预留设备能力假报为可用。"""
        values=["device.probe"]
        values.extend(self.watcher.available_capabilities())
        if self.config.get("shellEnabled"):
            values.append("shell.exec")
        if self.player:
            values.append("music.play")
        return values

    def run_forever(self):
        """无参数；启动本机接口和串行执行器，持续签名心跳并维护待发箱。"""
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        threading.Thread(target=self.execute_commands,daemon=True).start()
        last_scan=0
        delay=3
        while not self.stopped.is_set():
            try:
                if time.monotonic()-last_scan>15:
                    self.watcher.scan_sources(self.settings)
                    last_scan=time.monotonic()
                events,results=self.journal.pending_items()
                body={"op":"heartbeat","bootId":self.boot,"platform":platform.system(),
                    "capabilities":self.capabilities(),"events":events,"results":results}
                raw=self.transport.exchange_message(self.identity.sign_envelope(self.config["deviceId"],body))
                reply=DeviceIdentity.verify_envelope(self.config["serverPublicKey"],raw,self.config["deviceId"])
                if not reply.get("ok"):
                    if reply.get("conflict"):
                        self.write_status(False,"DeviceInstanceConflict")
                        return False
                    raise ValueError("服务器拒绝心跳")
                self.settings=reply["settings"]
                self.journal.acknowledge_items(reply["accepted"],reply.get("acceptedResults",[]))
                for command in reply["commands"]:
                    with self.command_lock:
                        if command["id"] not in self.running:
                            self.running.add(command["id"])
                            try:
                                self.commands.put_nowait(command)
                            except queue.Full:
                                self.running.discard(command["id"])
                                break
                self.write_status(True)
                delay=3
            except Exception as exc:
                self.write_status(False,type(exc).__name__)
                delay=min(delay*2,30)
            self.stopped.wait(delay)

    def execute_commands(self):
        """无参数；先持久抢占再执行，重复投递只上报原结果，绝不自动重执行。"""
        while not self.stopped.is_set():
            try:
                command=self.commands.get(timeout=1)
            except queue.Empty:
                continue
            try:
                if self.journal.claim_command(command):
                    try:
                        result=self.executor.run_command(command)
                    except Exception as exc:
                        result={"ok":False,"error":"终端执行失败："+type(exc).__name__}
                    self.journal.finish_command(command["id"],result)
            finally:
                with self.command_lock:
                    self.running.discard(command["id"])
                self.commands.task_done()

    def write_status(self,connected,error=""):
        """connected/error为当前连接状态；写不含凭据的诊断，供安装器真实验收。"""
        path=Path(self.config["runtimeDir"])/"status.json"
        temp=path.with_suffix(".tmp")
        temp.write_text(json.dumps({"connected":connected,"checkedAt":time.time(),
            "deviceId":self.config["deviceId"],"pid":os.getpid(),"error":error}),encoding="utf-8")
        temp.replace(path)
