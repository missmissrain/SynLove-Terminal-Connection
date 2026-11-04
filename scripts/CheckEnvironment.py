"""终端环境验证：检查已有依赖，不执行安装、不启动任何服务。

无注册参数；允许从本组件runtime/dependencies复用已部署的依赖。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime/dependencies").is_dir():
    sys.path.insert(0, str(ROOT / "runtime/dependencies"))


def main():
    """无参数；真实导入必要运行依赖，导入失败以非零状态结束。"""
    import cryptography
    import websocket
    print("终端连接运行依赖可用。")


if __name__ == "__main__":
    main()
