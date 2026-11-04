"""终端一键登记：读取一次性接入文件，生成本机Ed25519密钥并回传公钥完成配对。

参数bundle为接入文件，root默认为仓库目录；现有身份优先复用，不覆盖私钥或重复使用配对码。
"""
import argparse
import json
import secrets
import shutil
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from terminalConnection.security.DeviceIdentity import DeviceIdentity
from terminalConnection.transport.RelayTransport import RelayTransport
from terminalConnection.server.DeviceProfile import DeviceProfile


def main():
    """无参数；配对成功并验证服务器签名后原子保存终端配置，失败保留本机密钥供核查。"""
    parser=argparse.ArgumentParser()
    parser.add_argument("--bundle",required=True)
    args=parser.parse_args()
    path=ROOT/"config/TerminalConfig.json"
    if path.exists():
        print("已有终端身份，复用配置；未重复配对。")
        return
    bundle=json.loads(Path(args.bundle).read_text(encoding="utf-8-sig"))
    if bundle.get("protocol")!="synlove-terminal-v1":
        raise ValueError("接入文件协议不匹配")
    if not DeviceProfile.supports_direct_client(bundle.get("kind","windows"),bundle.get("transport","direct")):
        raise ValueError("此安装器仅支持直连终端；ADB和嵌入式设备目前只允许预登记")
    config={key:bundle[key] for key in ("deviceId","relayURL","relayToken","serverPublicKey")}
    config.update({"runtimeDir":str(ROOT/"runtime/client"),"keyPath":str(ROOT/"privateKeys/DeviceKey.pem"),
        "port":17863,"token":secrets.token_urlsafe(32),"shellEnabled":True,
        "ffplay":shutil.which("ffplay") or "ffplay","volume":35})
    if bundle.get("tlsCaPem"):
        ca=ROOT/"config/RelayCa.pem"
        ca.write_text(bundle["tlsCaPem"],encoding="utf-8")
        config["tlsCaFile"]=str(ca)
    identity=DeviceIdentity(config["keyPath"])
    response=RelayTransport(config).exchange_message({"op":"pair","pairCode":bundle["pairCode"],"publicKey":identity.public_key()})
    reply=DeviceIdentity.verify_envelope(config["serverPublicKey"],response,config["deviceId"])
    if not reply.get("ok"):
        raise ValueError("服务器未确认登记")
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(".tmp")
    temp.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(path)
    print("终端密钥已在本机生成，公钥登记成功。")


if __name__=="__main__":
    main()
