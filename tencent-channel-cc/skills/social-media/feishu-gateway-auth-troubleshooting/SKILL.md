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

### 原因：FEISHU_ALLOWED_USERS 为空

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

## 相关 Skill

- `tencent-channel`：腾讯频道 CLI 操作（发帖、管理）
- `tencent-channel-init`：腾讯频道 Bot 鉴权失败（retCode=100007）排错
