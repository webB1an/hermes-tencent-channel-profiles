---
name: mengde-video-poster
description: 孟德严选视频搬运脚本 — 从抖音/小红书/快手下载无水印视频，随机轮询发到5个频道主频道（自拍摄影圈/孟德严选/女友控/忏悔一切/肉腿控）
version: 3.1.0
---

# 孟德严选视频搬运脚本

## 脚本位置
`/root/.hermes/profiles/tencent-channel-cc/scripts/mengde_video_poster.py`

## 用法

**CLI 模式（推荐，直接传参）：**
```bash
python mengde_video_poster.py "抖音分享文案"
```

**stdin JSON 模式（Cron 用）：**
```bash
python - <<'PYEOF'
import subprocess, json
proc = subprocess.Popen(
    ["python", "mengde_video_poster.py", "--stdin"],
    stdin=subprocess.PIPE, text=True
)
proc.communicate(input=json.dumps({"share_text": "抖音分享文案..."}))
PYEOF
```
或者直接构造 JSON 后 pipe。

## 已知问题

### stdin 模式空输入会 crash
`json.loads(sys.stdin.read())` 在 stdin 为空时报 `Expecting value` 错误。

**workaround**：始终使用 CLI 模式传参，不要用 stdin 模式。

### 平台识别
- 抖音：链接含 `douyin.com` → 用 `--backend native` 无 cookie 模式
- 小红书：链接含 `xiaohongshu.com` / `xhslink.com` / `xhs.com`
- 快手：链接含 `kuaishou.com` / `chenzhongtech.com`

### 文件名超长问题（errno 36: File name too long）
Linux 对单个文件名字符数有限制（约255），加上 temp dir 前缀后更容易触发。
**抖音** `sanitize_filename` 截断值已设为 60（不是 90），快手/小红书暂无截断但若也超长可如法炮制。
表现为下载成功但保存时报错 `Errno 36`，修复方式：减小对应平台脚本的 `sanitize_filename` 截断值。

### 各平台文件名获取机制（重要调试依据）
- **抖音**（douyin.py 第191行）：`desc` 字段 → fallback `douyin_{aweme_id}`。若视频 desc 全是 `#标签`，sanitize 后内容为空，触发 fallback，产生丑文件名。
- **快手**（kuaishou.py 第262行）：`title` 字段 → fallback `kuaishou_{content_id}`。正常时文件名=视频标题。
- **小红书**：类似逻辑，fallback 为平台 ID。

当文件名是纯时间戳格式（如 `douyin_7638993113198305466`），说明 API 未返回有效标题，此时应发纯视频。

### Linux 文件名超长问题（已修复）
**症状**：`[Errno 36] File name too long`，视频标题超长时触发。

**根因**：`sanitize_filename` 截断到 90 字符，但完整路径 `/tmp/mengde_video_xxx/<90字符>.mp` 超出了 Linux 255 字符文件名限制（`/` 路径符不计入文件名，但 temp dir 前缀 + 扩展名占用了一定长度）。

**修复**：将 `douyin.py` 中 `sanitize_filename` 的截断值从 `90` 改为 `60`。
- 文件：`remove-short-videos-watermark/douyin.py`，第 191 行
- `return name[:90]` → `return name[:60]`

### 文案生成逻辑（v1.2 改进）
1. 取视频文件名（去扩展名）
2. 去除 `#` 及其后续内容（如 `Jk返场#十柒2.0#微胖#jk卡点` → `Jk返场`）
3. **关键优化**：若文件名含平台关键词（douyin / dy / xiaohongshu / xhs / kuaishou / ks），说明是平台 fallback 的时间戳ID，此时**发纯视频（文案置空）**，避免丑丑的 ID 号出现在频道里

### `get_channel_id_for_guild` API 字段名
**实测**：`channels` 不是 `channel_list`，写错会导致 fallback 到 "1"。

### `input()` 在非交互式上下文会 EOF 导致脚本崩溃
脚本早期含 `input()` 交互读取，已删除。当前为**全自动随机轮询**，无需任何交互，直接运行即可。

## 频道主频道池（随机轮询）
| 频道名 | guild_id |
|---|---|
| 自拍摄影圈 | 664279424082167719 |
| 孟德严选 | 670516334082074035 |
| 女友控 | 584303044082165170 |
| 忏悔一切 | 661081054082166997 |
| 肉腿控 | 46486561778743039 |

### 轮询机制
- 状态文件：`~/.tmp/mengde_round_robin.json`（pool 列表）
- 每次发帖**自动**从池中随机选一个频道并移除
- 池空自动重置并打乱，无需人工干预
