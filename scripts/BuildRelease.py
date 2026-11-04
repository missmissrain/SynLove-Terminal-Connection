"""终端组件纯净源码归档：只收录代码、文档、测试与示例配置，排除运行数据和接入文件。

参数output为目标ZIP绝对路径；默认输出dist/SynLove-Terminal-Connection.zip。
"""
import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BuildRelease:
    """用显式目录清单构建部署代码包，避免将私有身份和凭据混入归档。"""

    def build_package(self, output):
        """output为目标文件；返回归档数量，不读取真实私有配置内容。"""
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        files = []
        for name in ("terminalConnection", "remoteControl", "scripts", "tests", "docs"):
            for file in (ROOT / name).rglob("*"):
                if (file.is_file() and "__pycache__" not in file.parts
                    and file.suffix.lower() in (".py", ".ps1", ".cmd", ".md", ".mjs", ".js", ".css")):
                    files.append(file)
        files.extend((ROOT / "config").glob("*.example.json"))
        files.extend(file for file in (ROOT / ".gitignore", ROOT / "Install.cmd",
                                      ROOT / "requirements.txt", ROOT / "README.md") if file.is_file())
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(set(files)):
                archive.write(file, file.relative_to(ROOT).as_posix())
        return len(set(files))


def main():
    """无传入参数；解析命令行目标并构建不含运行身份的代码包。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "dist/SynLove-Terminal-Connection.zip"))
    args = parser.parse_args()
    print("纯净源码归档完成，文件数：" + str(BuildRelease().build_package(args.output)))


if __name__ == "__main__":
    main()
