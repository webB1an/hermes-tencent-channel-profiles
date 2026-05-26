---
name: tencent-channel-init
description: 腾讯频道 CLI (tencent-channel-cli) 初始化与排错 — token 格式、环境路径、鉴权失败(retCode=100007)的常见原因。涉及腾讯频道 Bot 部署时优先使用。
homepage: https://connect.qq.com/ai
version: 1.0.0
---

# 腾讯频道 CLI 初始化与排错

## 1. CLI 安装检查

```bash
tencent-channel-cli --version           # 需要 >= 1.0.2
npm install -g tencent-channel-cli      # 未安装时
```

## 2. Token 配置（关键！）

**Token 必须包含 `bot:` 前缀**，否则 API 返回 `retCode=100007 invalid Authorization header`。

正确格式：
```
QQ_AI_CONNECT_TOKEN="bot:v1_<你的完整token>"
```

错误格式（少 `bot:` 前缀）→ 鉴权失败：
```
QQ_AI_CONNECT_TOKEN="v1_<token>"    # ❌ 缺少 bot: 前缀
```

配置路径：`<HERMES_HOME>/.qqcli/.env`

## 3. 验证登录

```bash
tencent-channel-cli token verify
# → {"data":{"message":"已登录，服务连通正常。","valid":true}}  ✅
# → retCode=100007 ❌ → 检查 token 是否带 bot: 前缀
```

## 4. 环境变量与路径

tencent-channel-cli 相关脚本的路径约定（profile: `<当前 HERMES_HOME>`）：

`.env` 文件位置：`<HERMES_HOME>/home/.qqcli/.env`（Token 凭证存放处）

## 5. 常见错误

| 错误 | 原因 | 解决 |
|------|------|------|
| `retCode=100007` | token 缺少 `bot:` 前缀 | 确认 `.env` 中 token 格式为 `bot:v1_xxx` |
| `retCode=8011` | token 过期或无效 | 重新生成 token |
| `Could not resolve host: api.qqc.qq.com` | 网络/DNS 问题 | 检查代理或网络连通性 |
| 业务探测失败 | Bot 应用未开通对应 API 权限 | 在开放平台确认 App 权限 |

## 6. CLI 常用命令

```bash
tencent-channel-cli doctor                  # 自检连通性
tencent-channel-cli token setup '<token>'  # 直接配置 token
tencent-channel-cli schema <domain>.<action> # 查看命令参数
```
