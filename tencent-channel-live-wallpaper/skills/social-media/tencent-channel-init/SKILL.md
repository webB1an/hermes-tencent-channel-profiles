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

**注意**：`~` 在不同 shell session 中展开可能不同（取决于 HERMES_HOME 和 $HOME 环境变量）。始终用绝对路径操作 profile 目录。

腾讯频道 CLI 相关脚本的路径约定（常见 profile）：

| Profile 名 | HERMES_HOME | .env 位置 |
|-----------|------------|-----------|
| `tencent-channel-june` | `/root/.hermes/profiles/tencent-channel-june` | `<HERMES_HOME>/.qqcli/.env` |
| `tencent-channel-live-wallpaper` | `/root/.hermes/profiles/tencent-channel-live-wallpaper` | `<HERMES_HOME>/home/.qqcli/.env` |

常见 profile 目录：
- 壁纸 Bot：`/root/.hermes/profiles/tencent-channel-live-wallpaper`
- 壁纸库 Bot：`/root/.hermes/profiles/tencent-channel-june`

live-wallpaper-download 下载脚本路径：
```
/root/.hermes/profiles/tencent-channel-live-wallpaper/scripts/live-wallpaper-download/scripts/
├── download-wallpaperwaifu-first-page.mjs
├── download-moewalls-first-page.mjs
└── download-desktophut-first-page.mjs
```
⚠️ **Linux 适配**：这些脚本原始版本硬编码 `curl.exe`，需将 `spawn("curl.exe")` 改为 `spawn("curl")` 才能在 Linux 运行。

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
tencent-channel-cli token verify           # 验证登录状态
tencent-channel-cli token setup '<token>' # 直接配置 token
tencent-channel-cli schema <domain>.<action> # 查看命令参数
```

### doctor 输出解读

```json
{"name":"系统密钥链","pass":false,"detail":"密钥链不可用: exec: \"dbus-launch\"..."}
```
→ ⚠️ 正常，降级到 dotenv 存储，不影响功能。

```json
{"name":"登录状态","pass":true,"detail":"来源: dotenv"}
```
→ ✅ Bot 已通过 dotenv 存储的 token 登录。

```json
{"name":"服务端点","pass":true,"detail":"端点可达 (405)"}
```
→ ✅ 网络连通，服务端点正常。

```json
{"name":"业务探测","pass":true,"detail":"服务调用正常"}
```
→ ✅ Bot 应用已开通对应 API 权限。

### token verify 输出

```json
{"data":{"message":"已登录，服务连通正常。","tokenSource":"dotenv","valid":true}}
```
→ ✅ 完全正常，token 有效且服务连通。

## 7. live-wallpaper-download 脚本

**仓库**：https://github.com/webB1an/live-wallpaper-download/tree/linux

已推送 `linux` 分支，适配了 `curl.exe` → `curl`。用法：

```bash
node /root/.hermes/profiles/tencent-channel-live-wallpaper/scripts/live-wallpaper-download/scripts/download-wallpaperwaifu-first-page.mjs --dry-run
```

推送到 GitHub 的方法（使用 PAT）：

```bash
cd <repo>
git checkout -b linux
# 修改 curl.exe → curl
git add . && git commit -m "fix: Linux compatibility"
git remote set-url origin https://<PAT>@github.com/<user>/<repo>.git
git push -u origin linux
```
