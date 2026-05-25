---
name: feishu-gateway-auth-troubleshooting
description: 飞书（Feishu）Gateway 接入认证排错 — 诊断 "配对不成功"、"Unauthorized user"、无法收发消息等问题。适用于 Hermes Gateway + Feishu adapter 配置。
category: social-media
version: 1.0.0
---

# 飞书 Gateway 认证排错

## 症状

飞书 bot 配对/连接不成功，消息发送失败，提示 "Unauthorized user" 或无法接收消息。

## 诊断步骤

### 1. 读取 agent 日志查找关键词

```bash
tail -500 /root/.hermes/profiles/<profile_name>/logs/agent.log | grep -i "Unauthorized\|allow\|feishu\|pair"
```

典型错误模式：
```
WARNING gateway.run: Unauthorized user: ou_xxxxxxxx (None) on feishu
```

`None` 表示该用户不在 allowlist 中。

### 2. 检查 profile 配置

在 profile 根目录的 `.env` 文件中，检查以下变量：

| 变量 | 值 | 含义 |
|------|-----|------|
| `FEISHU_ALLOW_ALL_USERS` | `true` | 允许所有飞书用户 |
| `FEISHU_ALLOWED_USERS` | `ou_xxx,ou_yyy` | 白名单用户 ID 列表（逗号分隔） |
| `FEISHU_APP_ID` | `cli_xxx` | 飞书 bot App ID |

### 3. 确认用户飞书 ID

在日志中搜索用户消息，找到 `chat_id=oc_xxx` 和用户 ID（格式：`ou_xxx`）。

## 常见原因

### 原因 1：FEISHU_ALLOWED_USERS 为空

```
FEISHU_ALLOW_ALL_USERS=false
FEISHU_ALLOWED_USERS=
```

**结果**：没有用户被授权，所有消息被拒绝。

**修复**：修改 `.env` 后重启 gateway：

```bash
# 方案A：允许所有用户
FEISHU_ALLOW_ALL_USERS=true

# 方案B：只允许特定用户（更安全）
FEISHU_ALLOWED_USERS=ou_目标用户ID
```

### 原因 2：Gateway 进程已退出（最常见但最容易被忽略！）

Gateway 会因各种原因崩溃退出，但 `gateway.pid` 文件还在。

**诊断**：
```bash
# 检查 gateway 进程是否存活
ps -p $(cat /root/.hermes/profiles/<profile>/gateway.pid) 2>/dev/null && echo alive || echo dead

# 查看 gateway_state
cat /root/.hermes/profiles/<profile>/gateway_state.json
# → "gateway_state": "running" = 正常运行
# → pid 不存在或 state 不是 "running" = 进程已死

# 也可检查 systemd
systemctl status hermes-gateway-<profile>.service --no-pager
```

**重要**：大部分生产环境的 gateway 由 **systemd 管理**（`hermes gateway install` 会注册为 service），不能用 kill/nohup 手动拉起，必须走 systemd。

**修复**：
```bash
# 方案A（推荐）：用 hermes CLI 走 systemd 重启
cd /root/.hermes
HERMES_HOME=/root/.hermes/profiles/<profile> \
  python hermes-agent/hermes_cli/main.py gateway restart --profile <profile>

# 方案B：直接操作 systemd
systemctl restart hermes-gateway-<profile>.service

# 方案C：手动拉起（仅当 systemd 不可用时，且必须确保原进程已 kill）
kill $(cat /root/.hermes/profiles/<profile>/gateway.pid) 2>/dev/null
sleep 1
cd /root/.hermes/profiles/<profile>
HERMES_HOME=/root/.hermes/profiles/<profile> \
  nohup /root/.hermes/hermes-agent/venv/bin/python \
  /root/.hermes/hermes-agent/hermes_cli/main.py \
  --profile <profile> gateway run --replace &
```

> ⚠️ 用 nohup 手动拉起会绕过 systemd 的进程管理，systemctl 会认为服务已停止并尝试重启，导致服务陷入 restart 循环。

### 原因 3：CLI 与 Gateway 的 Pairing 目录隔离（Architecture Bug）

`hermes pairing approve` CLI 命令操作的是 `~/.hermes/platforms/pairing/`（全局），
而 Gateway（profile 模式）操作的是 `<HERMES_HOME>/platforms/pairing/`（profile 私有）。
**两边文件互不可见！**

**诊断**：
```bash
# CLI 看到的是全局目录（这里没有 pending = CLI 认为没配对请求）
hermes pairing list

# Gateway 看到的是 profile 目录
cat /root/.hermes/profiles/<profile>/platforms/pairing/feishu-pending.json
cat /root/.hermes/profiles/<profile>/platforms/pairing/feishu-approved.json
```

**直接手动 approve（无需等待用户重新触发配对码）**：

如果 pending 中已有用户的配对码，可以直接将其移入 approved，跳过重新发消息的步骤：

```python
import json, time
from pathlib import Path

profile = Path('/root/.hermes/profiles/<profile>/platforms/pairing')
user_id = 'ou_xxxxxxxx'  # 要授权的用户 ID

# 1. 写入 approved
approved_file = profile / 'feishu-approved.json'
approved = json.loads(approved_file.read_text()) if approved_file.exists() else {}
approved[user_id] = {'user_name': '', 'approved_at': time.time()}
approved_file.write_text(json.dumps(approved, indent=2))

# 2. 从 pending 删除（避免重复）
pending_file = profile / 'feishu-pending.json'
if pending_file.exists():
    pending = json.loads(pending_file.read_text())
    for code in list(pending.keys()):
        if pending[code].get('user_id') == user_id:
            del pending[code]
    pending_file.write_text(json.dumps(pending, indent=2))

# 3. 清除 rate limit（防止静默丢弃）
rate_file = profile / '_rate_limits.json'
rate_limits = json.loads(rate_file.read_text()) if rate_file.exists() else {}
key = f'feishu:{user_id}'
if key in rate_limits:
    del rate_limits[key]
rate_file.write_text(json.dumps(rate_limits, indent=2))

print(f'Approved: {user_id}')
```

> 注意：如果 pending 中的用户长期没有操作，配对码会自然过期（TTL），此时只需让他再发一条消息触发新码即可。

### 原因 4：Rate Limit 或 Lockout 生效

用户请求配对码太频繁（10 分钟内只能一次）或 5 次审批失败触发 lockout（1 小时）。

**诊断**：
```python
import time, json
from pathlib import Path
profile = Path('/root/.hermes/profiles/<profile>/platforms/pairing')
limits = json.loads((profile / '_rate_limits.json').read_text())
lockout = limits.get('_lockout:feishu', 0)
print(f'Lockout active: {lockout > time.time()} (expires in {max(0, lockout - time.time()):.0f}s)')
```

**修复**：见原因 3 的 rate limit 清除步骤。

## 相关 Skill

- `tencent-channel`：腾讯频道 CLI 操作（发帖、管理）
- `tencent-channel-init`：腾讯频道 Bot 鉴权失败（retCode=100007）排错
