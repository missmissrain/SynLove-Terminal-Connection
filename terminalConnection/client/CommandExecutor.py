"""终端命令执行：按显式能力执行CMD或音乐，不调用模型，不自动重放失败指令。

注册参数player为可选MusicPlayer；shellEnabled默认False，输出保留32KiB，超时最长120秒。
"""
import os
import subprocess
import threading
import time


class CommandExecutor:
    """执行服务器已签名且已登记到本机日志的类型化指令。"""

    def __init__(self,player=None,shell_enabled=False):
        """player为本机音乐实例，shell_enabled为安装时明确开启的远控权限。"""
        self.player,self.shell_enabled=player,shell_enabled

    def run_command(self,command):
        """command含type与params；返回真实结果，拒绝未知能力或过期指令。"""
        if time.time()>float(command.get("expiresAt",0)):
            return {"ok":False,"error":"指令已过期，未执行"}
        kind=command.get("type")
        args=command.get("params") or {}
        if kind=="probe":
            return {"ok":True,"protocol":"synlove-terminal-v1"}
        if kind=="music":
            if not self.player:
                return {"ok":False,"error":"终端未配置音乐播放能力"}
            return self.player.execute_request({**args,"requestId":command["id"]})
        if kind!="cmd" or not self.shell_enabled:
            return {"ok":False,"error":"终端未开放该远控能力"}
        text=args.get("command")
        timeout=args.get("timeoutSec",30)
        if not isinstance(text,str) or not text.strip() or len(text)>12000 or "\0" in text:
            raise ValueError("命令格式错误")
        if type(timeout) is not int or not 1<=timeout<=120:
            raise ValueError("命令超时须为1至120秒")
        argv=["cmd.exe","/d","/s","/c",text] if os.name=="nt" else ["/bin/sh","-c",text]
        process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,creationflags=0x08000000 if os.name=="nt" else 0)
        output=bytearray()
        truncated=[False]

        def collect_output():
            """无参数；持续排空合并输出，超过32KiB仅丢弃后续内容。"""
            while True:
                part=process.stdout.read(1024)
                if not part:
                    break
                remaining=32768-len(output)
                output.extend(part[:max(remaining,0)])
                if len(part)>remaining:
                    truncated[0]=True
        thread=threading.Thread(target=collect_output,daemon=True)
        thread.start()
        timed_out=False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out=True
            process.kill()
            process.wait(timeout=5)
        thread.join(2)
        try:
            text=bytes(output).decode("utf-8")
        except UnicodeDecodeError:
            text=bytes(output).decode("gb18030",errors="replace")
        return {"ok":not timed_out and process.returncode==0,"exitCode":process.returncode,
            "timedOut":timed_out,"remoteMayStillRun":timed_out,"stdout":text,"truncated":truncated[0]}
