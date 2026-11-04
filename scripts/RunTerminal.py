"""终端组件统一启动入口：role为server或client，config为私有JSON路径。

server默认回环17864；client默认回环17863。推荐pythonw -X utf8登录自启。
"""
import argparse
import json
import socket
import sys
import threading
import time
import traceback
from http.server import HTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if (ROOT/"runtime/dependencies").is_dir():
    sys.path.insert(0,str(ROOT/"runtime/dependencies"))


def main():
    """无参数；读取配置后启动一个角色，异常落本机日志但不写报文或凭据。"""
    parser=argparse.ArgumentParser()
    parser.add_argument("--role",choices=["server","client"],required=True)
    parser.add_argument("--config",required=True)
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    if args.role=="client":
        from terminalConnection.client.TerminalAgent import TerminalAgent
        agent=TerminalAgent(config)
        try:
            if agent.run_forever() is False:
                raise RuntimeError("设备已有其他活动实例，本次未接管命令")
        finally:
            agent.stopped.set()
            if agent.player:
                agent.player.stop_playback()
            agent.http.server_close()
        return
    from terminalConnection.server.TerminalManager import TerminalManager
    from terminalConnection.server.RelayServerNode import RelayServerNode
    from terminalConnection.server.TerminalAdminHandler import TerminalAdminHandler
    manager=TerminalManager(config)
    node=RelayServerNode(manager,config)
    http=HTTPServer(("127.0.0.1",int(config.get("adminPort",17864))),TerminalAdminHandler,bind_and_activate=False)
    http.allow_reuse_address=False
    if hasattr(socket,"SO_EXCLUSIVEADDRUSE"):
        http.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
    http.server_bind()
    http.server_activate()
    http.manager,http.node,http.token=manager,node,config["adminToken"]
    threading.Thread(target=node.run_forever,daemon=True).start()
    threading.Thread(target=http.serve_forever,daemon=True).start()
    while True:
        try:
            manager.deliver_notifications()
        except Exception as exc:
            error=Path(config["runtimeDir"])/"NotificationError.json"
            error.write_text(json.dumps({"checkedAt":time.time(),"error":type(exc).__name__}),
                encoding="utf-8")
        time.sleep(2)


if __name__=="__main__":
    try:
        main()
    except Exception:
        log=ROOT/"runtime/StartupError.log"
        log.parent.mkdir(parents=True,exist_ok=True)
        log.write_text(traceback.format_exc(),encoding="utf-8")
        raise
