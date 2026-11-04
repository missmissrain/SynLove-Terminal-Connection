# 设备登记与接入预留

设备的名称、类型、接入方式、父设备关系与执行能力分别保存。设备名称可修改，设备 ID 作为持久身份和命令路由依据，不随名称变化。

## 设备类型

| `kind` | 设备类型 | 当前接入范围 |
| --- | --- | --- |
| `windows` | Windows 电脑 | Python 直连终端 |
| `linux` | Linux 主机 | Python 直连终端；Windows 安装器不适用 |
| `gateway` | 接入网关 | Python 直连终端；子设备转发适配器尚未实现 |
| `android` | 安卓设备 | 预留原生直连、ADB 和网关登记 |
| `embedded` | 嵌入式设备 | 预留原生直连及网关登记 |

## 接入方式

`transport=direct` 表示设备以自身身份连接服务器，不使用 `parentId`。

`transport=adb` 表示安卓设备通过另一台已登记设备接入，必须填写 `parentId`。父设备的在线状态不能证明安卓设备在线；后续 ADB 适配器还需检查授权、设备状态与命令响应。

`transport=gateway` 表示安卓或嵌入式设备通过网关接入，必须填写 `parentId`。登记关系不包含 MQTT、串口、蓝牙或厂商协议实现。

父设备必须已登记，服务器拒绝自引用、循环关系和超过32层的父设备链。已配对设备的身份及接入关系不能通过重新登记覆盖。

## 管理接口

管理接口默认监听 `127.0.0.1:17864`，请求必须携带服务器私有 `Bearer` 管理凭据。终端流量继续通过已有 VPS 中转，不另开公网管理端口。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /v1/deviceTypes` | 查询类型、接入方式、适配器是否实现 |
| `GET /v1/devices` | 查询设备与实际心跳状态 |
| `POST /v1/device/reserve` | 预登记，不创建公钥、接入码或在线状态 |
| `POST /v1/pair` | 为已支持的直连终端生成一次性接入文件 |

安卓 ADB 设备预登记请求：

```json
{
  "deviceId": "androidPhone",
  "name": "安卓手机",
  "kind": "android",
  "transport": "adb",
  "parentId": "tianxuanLaptop"
}
```

嵌入式子设备预登记请求：

```json
{
  "deviceId": "embeddedBoard",
  "name": "开发板",
  "kind": "embedded",
  "transport": "gateway",
  "parentId": "deviceGateway"
}
```

## 状态边界

设备列表返回 `identityReady`、`adapterSupported`、`online` 和 `status`：

- `reserved`：已登记，尚无配对身份。
- `offline`：已配对，未获得有效在线心跳。
- `online`：已配对、已启用、当前适配器已实现，且验证后的心跳有效。
- `disabled`：已停用。

设备能力来自已验证心跳，管理员预登记提交的 `online`、`capabilities` 或 `publicKey` 不会生效。未实现适配器的设备不能接收执行指令，不能通过直连心跳绕过预留限制。父设备在线不会使子设备自动在线。

当前交付仅包含安卓与嵌入式的登记契约及防误用校验；不包含 ADB 执行、安卓原生客户端、嵌入式固件或网关子设备路由。

## 更新记录

### 2026-11-04

新增安卓、ADB、嵌入式与父设备关系预留；集中设备字段校验；增加预登记及类型查询接口；区分预留、离线和在线状态；限制未实现适配器执行，并加入隔离数据库及本机 HTTP 验证。
