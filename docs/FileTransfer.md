# 设备间文件传输

欣爱通过终端 MCP 使用文件能力。先调用 `terminal_devices` 查询登记设备，再查看响应中的 `fileTargets`，匹配用户指定的设备。只配置终端心跳并不等于已经安装 SSH；没有 SSH 文件目标的设备不能显示为可传输。

## 工具

| 工具 | 参数 | 返回内容 |
| --- | --- | --- |
| `remote_file_targets` | 无 | 当前机器及已配置 SSH 目标，不联网 |
| `remote_file_list` | `target`、`path` | 指定绝对目录的前 200 项及截断标记 |
| `remote_file_transfer` | `source`、`sourcePath`、`target`、`targetPath` | 字节数、SHA-256、`verified` |

`source`、`target` 可以使用返回的 SSH 别名，也可以使用已通过 `deviceId` 绑定的设备 ID。`local` 永远表示此 MCP 进程所在的机器；生产环境中通常是运行欣爱的服务器，不代表用户聊天时使用的笔记本。

上传示例：

```json
{
  "source": "local",
  "sourcePath": "C:/Work/report.txt",
  "target": "exampleTerminal",
  "targetPath": "C:/Users/Public/report.txt"
}
```

交换源和目标即可下载。源、目标都为远端设备时，由 MCP 所在机器流式转发，不把完整文件缓存在内存或交给模型。

## SSH 接入

1. 在目标设备启用 SSH/SFTP，并通过现有隧道使服务器能够访问。
2. 复制 `config/remoteControl.example.json` 为私有 `config/remoteControl.json`。
3. 填写目标地址、端口、账号和已核验的 `hostKeySha256`。可用 `knownHostsPath` 替代或共同验证。
4. 私钥路径或密码仅写入私有 `config/secrets.json` 的 `remoteControl.targets.<别名>`，字段沿用 `privateKeyPath`、`password`、可选 `passphrase`。
5. 若需直接用设备 ID 传输，在目标项设置 `deviceId`，与 `terminal_devices` 的 ID 一致。

不更换现有 SSH 密钥，不新建公网 HTTP 文件接口。SFTP 与命令执行复用同一 SSH 入口；标准 SSH 端口为 22，隧道外部端口以私有配置为准。目标需支持 SFTP 子系统，仅有 CMD 命令执行权限不保证文件子系统可用。

## 内容与失败边界

- 默认最大单文件 256 MiB，默认总期限 180 秒，可通过 `timeoutSec` 设置 10～300 秒。
- `overwrite` 默认为 `false`。已存在文件会明确拒绝；不会因失败自动重传。
- 只支持普通文件。目录、符号链接和设备文件不会直接搬运。需要多文件时由执行者在授权范围内先打包。
- 父目录必须已经存在；Windows 路径可用 `C:/目录/文件`，也接受反斜杠。网络共享和相对路径不支持。
- 文件先写入目标同目录的随机 `.part` 暂存文件，读回核对字节数与 SHA-256，再原子发布。
- 覆盖远端文件需要 OpenSSH 的 `posix-rename` 扩展；不支持时明确失败，绝不通过删除原文件降级。
- 正常失败只清理本次暂存文件。若设备失联导致清理也失败，可能留下 `.part` 文件；原目标不会被主动删除。
- 若断线发生在原子提交阶段，应先查目标文件确认状态，不能把超时当作“肯定没写入”。

MCP 仅返回元数据和校验结果；搬运文件不会自动把正文放进欣爱的聊天上下文。如需分析内容，应先传到服务器，再由子 Agent 在任务上下文中读取。

## 验证

隔离测试：

```text
python -X utf8 -m unittest tests.FileTransferTests tests.TerminalMcpTests -v
```

真实小文件闭环：

```text
python -X utf8 scripts/VerifyFileTransfer.py --target exampleTerminal --remote-root C:/Users/Public
```

该命令只在指定父目录下创建随机测试目录，验证中文文件名、上传、下载、目录查询和默认禁止覆盖；远端测试文件保留供核查，不访问用户业务文件。
