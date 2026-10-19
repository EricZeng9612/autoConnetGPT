# Auto Connect GPT · autoConnetGPT

[![Tests](https://github.com/EricZeng9612/autoConnetGPT/actions/workflows/tests.yml/badge.svg)](https://github.com/EricZeng9612/autoConnetGPT/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**减少 Codex 断连后的反复排查，让手机远程连接 Mac 更省心。**

Auto Connect GPT 是一个面向中国大陆 macOS 用户的轻量级后台连接守护工具。如果你已经具备可用的网络代理，却仍经常遇到 Codex 连接不稳定、手机远程连接找不到电脑，或需要反复检查 VPN、切换节点、重新打开 ChatGPT，本程序可以帮助你按顺序定位问题，并在条件明确、安全可控时尝试恢复，减少回到电脑前手动处理的次数。

它会在登录后启动，立即检查一次，之后默认每小时巡检：**先确认网络和 VPN 代理，再检查 ChatGPT/Codex，最后判断 Remote 状态**。对无法确认的问题明确提示，不会把“进程还在”误报为“远程连接正常”。

> 非 OpenAI 官方产品。项目名 `autoConnetGPT` 按原始命名保留。本工具不提供 VPN、账号或服务访问资格，也不保证解决服务端故障、地区限制、账号权限或所有断连问题；使用时请遵守适用法律及服务条款。

## 能帮你解决什么

| 你遇到的情况 | 程序的处理方式 |
|---|---|
| 电脑空闲后睡眠，手机连接不到主机 | 守护期间维持防空闲睡眠断言；允许显示器正常熄屏 |
| 分不清是 Wi-Fi、VPN 还是 GPT 出问题 | 按“基础网络 → 当前代理客户端 → 代理链路 → 应用 → Remote”分层检测 |
| Clash 内核退出或本地代理端口失效 | 尝试启动，必要时正常退出并重启一次；受冷却限制 |
| 代理链路持续故障，需要手动换节点 | Clash 配置后优选新加坡节点并失败回滚；龙猫云提供检测结果和手动恢复 SOP |
| ChatGPT 未运行 | 在网络正常及恢复额度允许时启动 |
| App Server 或 Remote 异常 | 复检确认后，仅在能证明任务空闲时尝试正常重启；忙碌/未知则延后 |
| 问题无法自动修复 | 保留状态、动作记录及人工处理 SOP；状态变化时通知 |

## 龙猫云与 Clash 双模式

v2.2 保留两套代理适配，由 `config.json` 中的 `proxy_client` 选择。默认值为 `auto`。

| 设置 | 行为 |
|---|---|
| `auto`（推荐） | 每轮检查当前配置代理端口的实际监听进程，识别龙猫云或 Clash；不自动切换客户端 |
| `longma` | 明确使用龙猫云 Lite；检测代理链路、模型断流及应用/Remote，VPN 异常转人工 SOP |
| `clash` | 明确使用 Clash Verge；保留受限启动/重启、配置后新加坡节点优选与回滚 |

两种模式共享同一套应用/Remote 检查和任务空闲保护。**龙猫云模式不会调用 Clash 控制接口，不自动切换龙猫云节点或重启龙猫云**：目前尚未验证可安全使用的龙猫云控制接口。

端口归属不明确、多个监听者或指定模式与实际客户端不一致时停止恢复；只有一个应用窗口打开并不足以判定模式。自动模式下没有监听者时提示人工打开客户端，不猜测启动哪个。显式 `clash` 模式才允许在端口未占用时启动 Clash。切换过程中若发现客户端变化，本轮立即停止；遗留 Clash 回滚事务不会跨到龙猫云执行。

当前已验证的内核路径为 `/Applications/龙猫云_Lite.app/Contents/MacOS/lmclientCore` 和 `/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo`。其他版本/安装位置不冒认支持。`proxy_url` 必须与所用 HTTP/Mixed 代理端口一致；自动模式识别该端口，不扫描或修改系统代理、TUN、Codex 代理环境。

## 断流与 Remote 检测边界

**v2.1 可只读分析当前 App Server 的原生日志，识别 HTTP/SSE、模型 WebSocket、Remote 断流与上下文整理失败。** 日志绑定进程 PID 和启动时间，仅统计最近 5 分钟；主机 Remote 握手证据 120 秒后过期。没有日志、结构变化或证据不完整时保持未知，不推断手机在线。

程序仍没有可靠的内置“任务空闲”采集器，默认不会自动重启正在运行的 ChatGPT。需重启恢复时必须接入可信的任务空闲证据，格式见 [状态采集器接口](docs/EVIDENCE.md)。短请求通过、主机握手成功均不保证手机连接或长任务成功，不要用固定 JSON 或旧日志伪造状态。

`stream disconnected` 不一定是 WebSocket 单独故障。程序不会修改 Codex 的传输协议、写入旧版实验开关，或把 HTTP/1.1 当成通用解法。默认只报告重复断流；显式启用 `recover_stream_failures` 后，只有模型流与 Remote 在同一观察窗口都重复失败，才尝试受限节点优选。这是恢复尝试，不是对 VPN 根因的断言。

**自动切换节点默认未启用。** 必须配置你允许程序修改的一个 Mihomo `Selector` 代理组，以及确认位于新加坡的候选节点名单。本程序不改变订阅、不新增节点、不自动修改路由规则。该代理组必须实际影响 ChatGPT/Remote 的出站路径。

“最低延迟”指：**本轮配置候选中，通过双目标探测、对 ChatGPT 探测地址多次测得的中位延迟最低**，不是对所有供应商或所有时刻的保证。最快候选切换后验证失败，会回滚并尝试下一个；最多尝试三个。当前节点不作为“新节点”。

## 恢复流程

```mermaid
flowchart LR
    A[登录启动 / 每小时] --> B[基础网络与 VPN 代理]
    B -->|通过| S[检查近期模型流与 Remote 断流]
    S -->|无重复失败证据| C[ChatGPT / Codex App Server]
    S -->|重复失败| E
    C -->|通过| D[Remote 有效状态证据]
    B -->|失败或未知| E[网络恢复 / 明确提示]
    C -->|持续异常| F[空闲确认 + 冷却检查]
    D -->|两次独立失败| F
    D -->|未知 / 登录配对| G[复检 / 人工处理]
    F --> H[最多一次正常重启 + 全链路复检]
```

完整流程图已随仓库发布：[分阶段 SOP 流程图](docs/FLOWCHART.md)。将网络、节点选择、应用恢复拆分绘制，主线自上而下，避免回路线交叉。

## 安装与升级

环境要求：macOS、Python **3.9+**、当前用户已登录桌面；默认适配 `/Applications/ChatGPT.app`，代理支持 Clash Verge 或龙猫云 Lite，HTTP/Mixed 代理端口默认 `7897`。这不是独立 Codex.app 或任意 VPN 客户端的通用适配器。

```bash
git clone https://github.com/EricZeng9612/autoConnetGPT.git
cd autoConnetGPT
python3 --version
zsh install.zsh
```

如果找不到 Python，先自行安装 Python 3.9+。可用 `AUTOCONNETGPT_PYTHON=/绝对路径/python3 zsh install.zsh` 指定解释器。安装器固定解释器路径，避免登录后 PATH 不同而启动失败。无需管理员密码；macOS 可能要求允许自动化控制应用或显示通知。

升级已有安装：在仓库目录执行 `git pull --ff-only`，再运行 `zsh install.zsh`。**仅更新 GitHub 或拉取代码不会替换正在运行的版本。** 安装器会先停止旧后台服务，备份原安装及 LaunchAgent，保留已有 `config.json`、恢复历史和日志，再启动新版本；失败时会明确停止，不删除备份。

从 v1 升级：改用 `config.json` 配置，不再读取旧的 `AUTOCONNETGPT_CHECK_INTERVAL`、`AUTOCONNETGPT_PROXY_URL`、`AUTOCONNETGPT_PROXY_PORT`、`AUTOCONNETGPT_CHECK_URL` 运行参数。请把自定义端口、周期等迁移至 JSON。v1 日志保留在原来的 `~/Library/Logs/autoConnetGPT/`。

## 配置新加坡节点优选

以下仅用于 Clash，龙猫云模式不会使用这些节点配置。安装后编辑 `~/Library/Application Support/autoConnetGPT/config.json`，完整字段见 [配置示例](config.example.json)。下面只展示需要按实际情况修改的字段，其他字段保留：

```json
{
  "selector_group": "填写实际生效的手动选择组名",
  "singapore_nodes": ["填写新加坡节点完整名称一", "填写新加坡节点完整名称二"],
  "allow_singapore_name_match": false,
  "controller_socket": "auto",
  "controller_url": "http://127.0.0.1:9090",
  "controller_secret_file": ""
}
```

- 名称必须与 Clash 中完全一致。只支持该组的**直接叶子节点**，不递归修改嵌套策略组。
- 推荐明确的 `singapore_nodes` 白名单；节点地理位置由你/供应商确认。可选名称匹配识别 `新加坡`、`Singapore`、`SG` 等，但名称不等于真实出口位置，因此默认关闭。
- 默认最多 20 个候选，每节点 3 轮“双目标”测试，以 ChatGPT 目标延迟中位数排序。超过候选上限会要求缩小名单，不会悄悄取部分节点后宣称最低。
- `controller_socket: "auto"` 自动发现当前用户临时目录或旧版路径下的安全 Unix socket；都没有时才使用配置的 loopback HTTP 接口。显式指定的 socket 不存在时直接停止，不悄悄换控制器。控制接口不可访问/未授权不等同于内核故障。
- 切换前校验实际线路：全局模式沿 `GLOBAL` 当前选择链确认目标组；规则模式要求观察到 OpenAI 活动连接，且这些连接都经过目标组。无证据或流量分流时报告 `VPN_ROUTE_UNCONFIRMED`，不改代理模式/规则。切换全局组会影响其他经过该组的流量，请只授权你接受的组。
- 默认 ChatGPT 探测为 `/cdn-cgi/trace`，仅检查到该域名的 HTTPS 可达性，不检查登录、模型服务或长连接。已安装版本保留原配置，升级时需自行把旧 socket 改为 `auto`、旧首页探测地址改为示例值。
- 若控制接口需要密钥，将其放入当前用户拥有的独立文件，权限 `600`，在 `controller_secret_file` 填绝对路径。不要写入仓库或公开 issue。
- 探测收到 403、429 或 5xx 会报告“无法确认”，**不会一律判为健康，也不会因此切节点**。需要先人工辨别验证页面、服务端故障或权限问题。
- `basic_urls` 应是当前网络可直接访问的 HTTPS 地址；TUN/透明代理环境下，“不使用 HTTP 代理”不代表完全绕过 VPN，可能需要调整探测目标。

更改配置后需重启后台服务以重新加载：

```bash
launchctl kickstart -k "gui/$(id -u)/com.eric.autoConnetGPT"
```

此命令只重启本守护程序，不代表重启 ChatGPT。但它随后会立即按新配置检查。节点事务在每次修改前持久化；中途退出时会在下次检查优先完成回滚。

## 查看与诊断

```bash
# 最近状态（不会执行恢复）
"$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.zsh" --status

# 只读诊断：会发起网络探测，但不切节点、不重启应用、不改恢复历史
"$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.zsh" --diagnose

# 立即检查，允许按安全规则恢复
"$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.zsh" --check

# 仅看当前 App Server 最近的脱敏断流计数，不发送网络请求
"$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.zsh" --diagnose-transport

# 最近状态及当前电源设置/睡眠断言
"$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.zsh" --diagnostics

# 日志（每份最多 5 MB，保留两份轮转备份）
tail -f "$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.log"
```

程序、配置、`status.json`、`recovery.json` 和 v2 日志位于 `~/Library/Application Support/autoConnetGPT/`。LaunchAgent 为 `~/Library/LaunchAgents/com.eric.autoConnetGPT.plist`。恢复状态包含本地节点事务，**不要上传整个运行目录**。

| 主要状态 | 意义 / 下一步 |
|---|---|
| `HEALTHY` | 当前检查的代理、本地应用、有效 Remote 证据均通过；不是永久在线保证 |
| `NETWORK_UNAVAILABLE` | 优先检查基础网络、路由器、Wi-Fi 认证 |
| `VPN_PATH_UNKNOWN` | HTTP 返回结果不够确定；人工辨别，不自动切换 |
| `STREAM_UNSTABLE` | 近期存在重复断流/压缩失败；短请求或一次握手成功不足以确认恢复 |
| `VPN_ROUTE_UNCONFIRMED` | 目标组未证实控制实际线路；检查当前代理模式及规则 |
| `VPN_CLIENT_UNSUPPORTED` | 代理端口归属未知或不唯一；不抢占端口、不执行恢复 |
| `VPN_MODE_MISMATCH` | 指定模式不匹配或检查期间客户端变化；不切换客户端、不重启 GPT |
| `VPN_CLIENT_UNAVAILABLE` | 未发现代理监听；自动/龙猫云模式提示人工打开客户端 |
| `VPN_CONFIG_REQUIRED` / `VPN_NO_SG_NODE` | 需配置代理组/候选，或本轮没有通过测试的新加坡节点 |
| `VPN_CONTROLLER_UNAVAILABLE` | 检查本地控制接口和密钥权限 |
| `VPN_ROLLBACK_REQUIRED` / `VPN_SELECTION_CHANGED` | 回滚受阻或外部修改节点，停止自动操作 |
| `VPN_NEEDS_ATTENTION` | 有限恢复后代理仍异常 |
| `REMOTE_UNKNOWN` | 没有足够的新鲜 Remote 证据；不等于离线 |
| `REMOTE_MANUAL` | 需要登录、配对或手动启用 Remote |
| `RESTART_DEFERRED` / `TASKS_NEED_ATTENTION` | 无法证明任务空闲，延后或转人工 |
| `REMOTE_NEEDS_ATTENTION` | 本地应用/Remote 恢复后仍异常 |
| `COOLDOWN` / `RESTART_LIMIT` | 限制频繁恢复，等待冷却或人工排查 |
| `HOST_SLEEP_RISK` | 未观察到防睡眠断言；不直接改变系统电源设置 |
| `CHECK_ERROR` / `CHECK_TIMEOUT` | 配置、状态、权限或预算异常，安全停止 |

详细处理步骤见 [SOP](SOP.md)。

## 安全与恢复限制

- 单实例锁防止并发巡检。动作前记录持久化额度：Clash 30 分钟冷却、节点切换批次 6 小时冷却、ChatGPT 启动/重启 6 小时冷却且最多 2 次/24 小时。失败尝试同样计数。
- 每轮应用恢复最多一次；任务忙碌或未知时延后 10 分钟，最多两次，然后转人工。巡检总预算约 15 分钟，各网络请求也有超时。
- 不强制杀进程、不退出账号、不自动填写密码或验证码、不重置配对、不修改系统睡眠设置、不公开 App Server 端口。
- 屏幕熄灭不等于系统睡眠。`caffeinate -is` 用于维持后台主机唤醒，其中 `-s` 仅在接电时有效。合盖、手动睡眠、关机、断电仍可中断服务；建议远程主机接电运行。
- macOS 完全睡眠时脚本本身无法检查或自救，唤醒后才能继续。登录前也不会运行用户 LaunchAgent。
- 本地主机检查不能完整替代“手机 → 网络 → Remote 主机”的端到端验证；手机侧网络故障仍需在手机处理。

## 测试与卸载

```bash
python3 -m unittest discover -s tests -v
zsh -n autoConnetGPT.zsh install.zsh uninstall.zsh
zsh uninstall.zsh
```

卸载会停止服务，把安装目录和 LaunchAgent 移到带随机后缀的 `autoConnetGPT-uninstalled-*` 归档中，配置和日志可恢复，不进行递归删除。

## 参考与许可证

- [Mihomo 控制接口](https://wiki.metacubex.one/en/api/)：节点读取、延迟测试与选择操作。
- [OpenAI Remote connections](https://learn.chatgpt.com/docs/remote-connections)：Remote 使用说明；本项目没有假定存在未验证的本地健康 API。
- [安全说明](SECURITY.md) · [更新记录](CHANGELOG.md) · [MIT License](LICENSE)
