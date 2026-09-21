# autoConnetGPT

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

如果你经常用手机远程连接 Mac 上的 ChatGPT/Codex，却遇到电脑休眠、Clash/VPN 失效或应用异常退出后无法连接的问题，`autoConnetGPT` 可以帮你自动守护连接。它会在登录后启动，防止 Mac 因空闲进入睡眠，并定时检查网络代理、ChatGPT 和 Codex 的运行状态；发现异常时，会按照安全顺序尝试恢复 VPN 或重启相关服务，减少需要回到电脑旁手动处理的情况。

> 这是个人维护工具，不是 OpenAI 官方产品。项目名 `autoConnetGPT` 按原始命名保留。

## 功能

- 使用 macOS LaunchAgent 登录自启，并在守护程序异常退出时自动拉起。
- 使用 `caffeinate` 防止整机因空闲进入睡眠；显示器仍可正常熄灭。
- 检查 Clash Verge/Mihomo 进程和本地代理端口。
- 通过代理探测 ChatGPT 网络路径，所有请求均设置连接与总超时。
- 检查 ChatGPT 主进程和 Codex App Server。
- 优先修复 VPN；只有 VPN 正常但 App Server 缺失时才重启 ChatGPT。
- 使用单实例锁避免重复检查，并自动清理异常退出遗留的锁。
- 记录状态、修复动作和日志；日志超过 5 MB 后轮转一份。
- 仅在状态发生变化或需要人工处理时发送 macOS 通知。

## 恢复流程

```text
保持主机唤醒
    ↓
检查 Clash 进程与代理端口
    ↓
通过代理访问 ChatGPT
    ↓
启动或重启 Clash（如有必要）
    ↓
检查 ChatGPT 与 Codex App Server
    ↓
启动或重启 ChatGPT（仅在必要时）
    ↓
写入状态和日志
```

## 环境要求

- macOS
- `/Applications/ChatGPT.app`
- `/Applications/Clash Verge.app`
- Clash HTTP 代理默认监听 `127.0.0.1:7897`
- 当前用户可正常登录桌面会话

程序不读取或保存 Clash 订阅、节点凭据、ChatGPT 登录凭据或 GitHub 令牌。

## 安装

```bash
git clone https://github.com/EricZeng9612/autoConnetGPT.git
cd autoConnetGPT
zsh install.zsh
```

安装位置：

- 程序：`~/Library/Application Support/autoConnetGPT/`
- 后台服务：`~/Library/LaunchAgents/com.eric.autoConnetGPT.plist`
- 状态：`~/Library/Application Support/autoConnetGPT/status.txt`
- 日志：`~/Library/Logs/autoConnetGPT/`

安装不需要管理员密码。

## 使用

```bash
# 查看最近一次状态
"$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.zsh" --status

# 立即执行一次检查
"$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.zsh" --check

# 输出电源断言、相关进程和最近日志
"$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.zsh" --diagnostics

# 实时查看日志
tail -f "$HOME/Library/Logs/autoConnetGPT/autoConnetGPT.log"
```

## 状态码

| 状态 | 含义 |
|---|---|
| `HEALTHY` | 所有监控项正常 |
| `VPN_REPAIRED` | Clash/VPN 已自动恢复 |
| `CODEX_REPAIRED` | ChatGPT/Codex 已自动恢复 |
| `HOST_SLEEP_RISK` | 仍存在整机睡眠风险 |
| `VPN_NEEDS_ATTENTION` | VPN 无法自动恢复，需要人工切换节点 |
| `REMOTE_NEEDS_ATTENTION` | 本地网络可用，但 ChatGPT/Codex 无法自动恢复 |

详细人工处理步骤见 [SOP.md](SOP.md)。

## 可选配置

以下环境变量可覆盖默认配置：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `AUTOCONNETGPT_CHECK_INTERVAL` | `3600` | 检查周期，单位为秒 |
| `AUTOCONNETGPT_PROXY_URL` | `http://127.0.0.1:7897` | HTTP 代理地址 |
| `AUTOCONNETGPT_PROXY_PORT` | `7897` | 代理监听端口 |
| `AUTOCONNETGPT_CHECK_URL` | `https://chatgpt.com/` | 网络探测地址 |

如需永久修改，请在 LaunchAgent 模板的 `EnvironmentVariables` 中添加对应值后重新运行安装脚本。

## 卸载

```bash
zsh uninstall.zsh
```

卸载会停止后台服务并删除安装目录，历史日志会保留在 `~/Library/Logs/autoConnetGPT/`。

## 检测边界

- ChatGPT 当前没有向本地脚本公开“手机是否仍然配对”或“远程控制开关是否开启”的健康接口。本机检查全部正常但手机仍看不到主机时，请按照 SOP 检查“设置 → 连接”。
- LaunchAgent 需要用户登录后才能运行；它不能在尚未登录的开机界面恢复服务。
- 手动选择“睡眠”、关机、断电或网络设备故障仍会中断连接。
- 程序不会自动选择 Clash 节点，避免依赖或修改用户的代理订阅配置。
- 不要将 Codex App Server 直接暴露到公共互联网。OpenAI 建议跨网络访问时使用 VPN 或网状网络。

参考：[OpenAI Remote connections](https://learn.chatgpt.com/docs/remote-connections)

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
