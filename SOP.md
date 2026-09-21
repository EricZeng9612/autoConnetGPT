# autoConnetGPT 故障处理 SOP

## 自动处理顺序

1. 检查 Clash 代理端口 `127.0.0.1:7897`。
2. 通过该代理访问 `https://chatgpt.com/`；HTTP 200–499 视为网络已连通。
3. VPN 异常时先启动 Clash；仍失败才重启 Clash，最多执行两级恢复。
4. VPN 正常后检查 ChatGPT 主进程与 Codex App Server。
5. ChatGPT 未运行时启动；只有 VPN 正常但 App Server 缺失时才重启 ChatGPT。
6. 保存状态并仅在状态变化或需要人工处理时发送本机通知。

## 状态说明

- `HEALTHY`：所有检查正常。
- `VPN_REPAIRED`：已自动恢复 Clash/VPN。
- `CODEX_REPAIRED`：已自动恢复 ChatGPT/Codex。
- `HOST_SLEEP_RISK`：系统仍存在整机睡眠风险，且守护程序没有保持唤醒。
- `VPN_NEEDS_ATTENTION`：自动重启后仍无法通过代理访问 ChatGPT；请手动切换 Clash 节点。
- `REMOTE_NEEDS_ATTENTION`：本地网络可用，但 ChatGPT/Codex 无法恢复；检查登录、远程控制和设备配对。

## 人工处理

### VPN_NEEDS_ATTENTION

1. 打开 Clash Verge。
2. 确认系统代理开启、端口为 `7897`。
3. 切换到另一个可用节点。
4. 运行：

   ```bash
   "$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.zsh" --check
   ```

### REMOTE_NEEDS_ATTENTION

1. 打开 ChatGPT 桌面应用。
2. 进入“设置 → 连接 → 控制此 Mac”。
3. 确认“允许其他设备连接”和“保持此 Mac 唤醒”均已开启。
4. 确认手机和 Mac 使用相同账号及工作空间。
5. 如果刚重新登录过，重新开启远程控制；仍失败则重新扫描二维码配对。

## 常用命令

```bash
# 当前状态
"$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.zsh" --status

# 立即检查一次
"$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.zsh" --check

# 完整诊断
"$HOME/Library/Application Support/autoConnetGPT/autoConnetGPT.zsh" --diagnostics

# 查看日志
tail -f "$HOME/Library/Logs/autoConnetGPT/autoConnetGPT.log"
```

守护程序保持整机不因空闲进入睡眠，但不阻止显示器熄屏。手动选择“睡眠”、关机或断电仍会中断远程连接。
