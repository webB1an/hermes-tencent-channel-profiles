---
name: tencent-channel-live-wallpaper
description: 腾讯频道动态壁纸 Bot — 下载 WallpaperWaifu/MoeWalls/DesktopHut 视频壁纸，发帖到动态壁纸版块（channel_id=667049126），发帖后删除文件。包含 `video_paths` 参数发现、`--limit 1` 防超时、`.mp4.part` 残留清理、两层去重等关键试验结论。
homepage: 
version: 1.0.0
metadata: {}
---

# tencent-channel-live-wallpaper

腾讯频道动态壁纸 Bot 的自动化发帖 workflow。

## 核心脚本

**⚠️ Profile 已统一到 `tencent-channel-dupan-live-wallpaper`**（之前有两个 profile 混乱，已合并）

```
/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/scripts/bdpan_wallpaper_post.py
```

**使用方式：**
```bash
cd /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper
python3 scripts/bdpan_wallpaper_post.py
```

**触发词：** `发动态壁纸`

---

## 关键发现（试验得来，不要改）

### 1. 视频发帖用 `video_paths`，不是 `file_paths`

这是最关键的 API 发现。`tencent-channel-cli schema feed.publish-feed` 确认：
- **图片帖**：`file_paths`（对象数组，每个元素 `{"file_path": "/path/to/image.jpg"}`）
- **视频帖**：`video_paths`（字符串数组，每个元素为视频文件的绝对路径）

```python
# ✅ 正确
result = subprocess.run(
    ["tencent-channel-cli", "feed", "publish-feed",
     "--guild-id", GUILD_ID,
     "--channel-id", CHANNEL_ID,
     "--content", title,
     "--video", video_path,
     "--json"],
    ...
)

# ❌ 错误（图片帖参数，视频会被静默忽略）
result = subprocess.run(
    ["tencent-channel-cli", "feed", "publish-feed",
     ...
     "--file", video_path,  # file 是图片参数！
     ...
)
```

### 2. Node 下载脚本必须加 `--limit 1`（所有三个源均已支持）

Python 每次运行只下载 1 个视频：下载 → 发帖 → 删除 → 再运行下载下一个。原始 Node 脚本会下载全部 22 个壁纸（约 1GB+），导致超时。

```bash
node scripts/live-wallpaper-download/scripts/download-wallpaperwaifu-first-page.mjs --limit 1
```

`download-wallpaperwaifu-first-page.mjs` 已修改支持 `--limit` 参数（第 1 个成功后 `break`）。

### 3. Node `runCurl` 必须设置超时（30min），否则 curl --retry 会无限挂起

原始 `runCurl` 没有超时机制。当 curl 使用 `-C -`（断点续传）遇到 416 错误时，`--retry 6` 会不断重试，导致整个 Node 脚本Hang住。

**修复**：在 `download-wallpaperwaifu-first-page.mjs` 中：
```js
const CURL_DOWNLOAD_TIMEOUT_MS = 1800000; // 30 minutes
function runCurl(args) {
  return new Promise((resolve, reject) => {
    const child = spawn("curl", args, { stdio: ["ignore", "inherit", "inherit"] });
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`curl timed out after ${CURL_DOWNLOAD_TIMEOUT_MS / 1000}s`));
    }, CURL_DOWNLOAD_TIMEOUT_MS);
    child.on("error", (err) => { clearTimeout(timer); reject(err); });
    child.on("exit", (code) => {
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new Error(`curl exited with code ${code}`));
    });
  });
}
```

设置 30 分钟后，Page 1 的壁纸（如 Oddities Shelf 65MB）在 ~92 秒内即可下载完成，不再需要 `--page 2` 绕路。

### 4. Python 清理逻辑需同时删除不完整的 .mp4

curl 被 kill 后会留下不完整的 `.mp4` 文件（不是 `.part`）。判定标准：小于 1MB 视为不完整（正常视频 40MB+）。

```python
def _cleanup_resume_artifacts(self):
    for f in downloads_dir.iterdir():
        if f.suffix == ".part":
            f.unlink()
        elif f.suffix == ".mp4" and f.stat().st_size < 1024 * 1024:
            f.unlink()  # 不完整视频
```

### 5. `.mp4.part` 文件会阻止下载，且无法续传

Node 脚本使用 curl 的 `-C -`（断点续传）。如果之前运行失败，会留下 `.mp4.part` 残留文件。
两种情况：

**情况A — 服务器支持断点续传**：curl 请求旧 Range，服务器返回 416 → 清理后重下即可。
**情况B — 服务器不支持断点续传**（如 WallpaperWaifu）：curl 会输出 `HTTP server doesn't seem to support byte ranges. Cannot resume.` 并不断重试 → **必须删除 `.mp4.part` 才能开始全新下载**。

**每次运行前清理**：

```python
def _cleanup_resume_artifacts(self):
    for f in self.downloads_dir.glob("*.mp4.part"):
        f.unlink()
```

### 6. Manifest 有记录但文件丢失时——必须先检查 is_posted 才能清理 dedup

**⚠️ 关键教训**：manifest 状态为 `downloaded` 但本地文件不存在时（超时被 kill 后文件被清理），不能直接清 dedup。必须先检查 Python `PostedStore.is_posted()`：

- **已发帖的 URL**：文件丢失也**不能**从 dedup 移除，否则该 URL 会重新下载（浪费资源）
- **未发帖的 URL**：文件丢失才从 dedup 移除，让下次下载可以重新获取

```python
if not self.dry_run and not wallpaper.file_path.exists():
    if not self.store.is_posted(wallpaper.detail_url):
        self.downloader._remove_url_record(wallpaper.source, wallpaper.detail_url)
    return 3
```

如果把 `_remove_url_record()` 放在 `_parse_manifest()` 里调用（此时还不知道 `is_posted` 状态），会导致已发帖的 URL 被错误地从 dedup 移除，下次运行会重新下载这些已发帖的壁纸。

### 6b. Manifest item.status 过滤逻辑（MoeWalls 历史积压发现）

Node 下载脚本写入的 manifest 条目 `status` 字段含义：

| item.status | record.status | 含义 | Python 应处理 |
|-------------|--------------|------|--------------|
| `downloaded` | — | 正常下载完成 | ✅ 读取 |
| `skipped-existing` | — | URL 已在 dedup 中跳过 | ✅ 读取 |
| `skipped-detail-url` | `downloaded` | URL 已在 dedup 中有记录，但之前成功下载过（文件可能还在） | ✅ 读取（文件存在时） |
| `skipped-detail-url` | 其他 | URL 已在 dedup 中有记录且无文件 | ❌ 跳过 |

**关键**：`skipped-detail-url` 不一定意味着"跳过"——需要看 `record.status` 是否为 `downloaded`。MoeWalls 历史积压就是这么被漏掉的（3 条有文件但 `item.status=skipped-detail-url`）。

过滤时同时参考 `item.get("record", {}).get("status")`：

```python
top_status = item.get("status", "")
record_status = item.get("record", {}).get("status", "")
if top_status not in ("downloaded", "skipped-existing") and not (
    top_status == "skipped-detail-url" and record_status == "downloaded"
):
    continue  # 跳过
```

另外，manifest 条目的 `filePath` 和 `name` 也**优先从 `record` 里取**（`skipped-detail-url` 条目的顶层字段可能为空或不准确）。


### 7. 两层去重机制 + State 文件重置风险

| 层级 | 文件 | 用途 |
|------|------|------|
| Python 层 | `live_wallpaper_state.json` 的 `posted_detail_urls` | **主去重**，记录已发帖的壁纸详情页 URL |
| Node 层 | `config/downloaded-wallpaperwaifu-detail-urls.json` | 防止 Node 脚本重复下载同一壁纸 |

**⚠️ State 文件被清空会导致已发帖 URL 全部重新变"未发帖"**。2026-05-15 事件中，`live_wallpaper_state.json` 在 22:45:49 被重建（重置），导致之前发帖过的 URL 全部被当成新 URL，触发重复发帖 30+ 次。

**症状**：如果 `live_wallpaper_state.json` 的创建时间远早于 `downloaded-*-detail-urls.json`，说明 state 被重置过。需要检查 `posted_detail_urls` 是否为空或数量异常。

两个文件**都不要删除**，Python 脚本只写 `live_wallpaper_state.json`，不碰 Node 的 dedup 文件。

### 8. DesktopHut 在 Python subprocess 中卡住

DesktopHut 下载脚本在 `subprocess.run()` 中会挂起（stdin/PTY 问题），但在 bash 中正常。因此：
- `DRY_RUN_SUPPORTED = {"wallpaperwaifu"}`（只有 WallpaperWaifu 支持 dry-run）
- dry-run 模式下跳过 MoeWalls 和 DesktopHut

### 9. 标题中 "Live Wallpaper" 不能重复出现

WallpaperWaifu 的壁纸名本身已包含 "Live Wallpaper" 后缀（如 `Frieren Sousou no Frieren Live Wallpaper`）。直接翻译整个 name 会得到 `Frieren Sousou no Frieren 动态壁纸`，导致标题变成：

```
Frieren Sousou no Frieren Live Wallpaper · Frieren Sousou no Frieren 动态壁纸
```

**修复**：翻译前用正则去掉末尾的 "Live Wallpaper" 后缀（大小写不敏感）：

```python
import re
name_en_base = re.sub(r'\s+Live\s+Wallpaper\s*$', '', wallpaper.name, flags=re.IGNORECASE)
name_zh = self.translator.translate(name_en_base)
title = f"{name_en_base} · {name_zh}"
# 结果：Frieren Sousou no Frieren · 葬送的芙莉莲
```

### 10. Manifest 文件名映射（关键：MoeWalls 不是 manifest-moewalls.json）

各下载脚本写入的 manifest 文件名不一致，Python 脚本必须用正确的文件名才能读到数据：

| 下载源 | manifest 文件名 | Python 读取键 |
|--------|----------------|--------------|
| WallpaperWaifu | `manifest-wallpaperwaifu.json` | `manifest-wallpaperwaifu.json` |
| MoeWalls | `manifest.json` | `manifest.json` ⚠️ 不是 `manifest-moewalls.json` |
| DesktopHut | `manifest-desktophut.json` | `manifest-desktophut.json` |

Python 端硬编码了 `manifest-{source_key}.json`，对 MoeWalls 会读到一个不存在的文件，导致 MoeWalls 的已下载条目**永远被忽略**。在 `DownloadRunner` 中通过 `MANIFEST_NAMES` 映射表修复。

如果某个源报告"下载成功，获取 0 个条目"，首先检查 `config/` 目录下实际存在的 manifest 文件名是否与代码中预期的文件名一致。

### 12. DesktopHut 服务器当前网络不可达（服务器 IP 被墙）

DesktopHut (`desktophut.com`) 在 curl 中 12 秒即超时，服务器无法访问。DNS 能解析（66.70.207.122），TCP 握手卡住约 5 秒后被重置，**不是网站本身宕机，是这台服务器 IP 被目标墙了**。该源目前处于不可用状态。脚本行为：WallpaperWaifu → MoeWalls → DesktopHut（DesktopHut 超时时整体退出）。

### 13. 腾讯频道 API 频率限制（20063）及冷却策略

发帖时可能遇到错误码 `20063`（"操作过于频繁，请稍候再试"）。实测发现：

- **5 分钟冷却不够**：等待 300 秒后立即发帖仍报 20063
- **需要更长冷却**：建议间隔 10–15 分钟再重试
- **小文件也可能触发**：13MB 的 "Night Melt" 在冷却不足时也失败了
- **触发后短期内的请求都会失败**，即使文件很小

**策略**：批量发帖时每发 1–2 个视频后等待 60 秒，出现 20063 后等待 600 秒再继续。已发帖记录在 `live_wallpaper_state.json`，重启后可从断点继续。

### 14. 上传超时阈值（300s 对大文件不够）

腾讯频道上传速度约 1–2 Mbps。实测超时阈值：

| 文件大小 | 建议超时 | 实测结果 |
|---------|---------|---------|
| < 50MB | 120s | ✅ 正常 |
| 50–100MB | 300s | ⚠️ 临界，可能超时 |
| > 100MB | 600s+ | ❌ 300s 必定超时 |

**已超时的文件**：103MB、132MB 的视频在 300s 内未完成上传，进程被 kill。已将此类文件移入 `downloads/pending/` 子目录，等待后续处理或分段上传。

### 15. Manifest 被 `--page` 覆盖时的三层检查法

当用 `--page 2` 等参数运行 Node 下载脚本时，**manifest 文件会被完全覆盖**，导致 Page 1 已下载的条目丢失。此时 Python 脚本无法仅凭 manifest 判断文件是否需要重新下载。

**三层检查法**（优先级从高到低）：

1. **`live_wallpaper_state.json` 的 `posted_detail_urls`**：已发帖的 URL 直接跳过（最优先）
2. **`downloaded-{source}-detail-urls.json`（Node dedup 文件）**：有记录说明文件曾存在；检查 `filePath` 字段指向的文件是否还在
3. **文件系统**：`downloads/` 目录下是否有对应视频文件

```python
# 判断是否需要发帖的伪代码
if detail_url in posted_detail_urls:
    skip("已发帖")
elif os.path.exists(video_file_path):
    post()  # 文件存在且未发帖 → 发帖
else:
    skip("文件不存在，dedup 已有记录，跳过")
```

**不要只靠 manifest 判断**——manifest 会被 Node 脚本覆盖，但 dedup JSON（`downloaded-wallpaperwaifu-detail-urls.json`）保留有 `filePath` 字段，是恢复孤儿文件的关键。

### 16. 各源分页条目数量（实测）

| 来源 | Page 1 | Page 2 | Page 3+ |
|------|---------|--------|---------|
| WallpaperWaifu | 22 条 | 22 条 | 未探索 |
| MoeWalls | 11 条 | 14 条 | 未探索 |
| DesktopHut | 不可达 | — | — |

Node 下载脚本支持 `--page N` 参数，调用示例：
```bash
# 下载 WallpaperWaifu Page 2
node scripts/live-wallpaper-download/scripts/download-wallpaperwaifu-first-page.mjs --page 2 --limit 1

# 下载 MoeWalls Page 2
node scripts/live-wallpaper-download/scripts/download-moewalls-first-page.mjs --page 2 --limit 1
```

Python 脚本 `live_wallpaper_post.py` **不支持 `--page` 参数**，扩展方案：循环调用不同 page，或直接用 Node+Python 混合模式（Node 下载 → Python 发帖）。

### 17. 大文件 Pending 处理流程

超过 ~100MB 的视频在当前上传速度下无法在 300s 内完成，需要特殊处理：

```bash
# 1. 将大文件移入 pending 子目录
mkdir -p downloads/pending
mv "downloads/超大视频.mp4" downloads/pending/

# 2. 待上传条件改善后，用 600s 超时重试
python3 -c "
import subprocess, json, time
# 用 600s timeout 上传
result = subprocess.run(['tencent-channel-cli', 'feed', 'publish-feed', ...], timeout=600)
"

# 3. 发帖成功后更新状态
python3 -c "
s = json.load(open('live_wallpaper_state.json'))
s['posted_detail_urls'].append('https://...')
with open('live_wallpaper_state.json','w') as f:
    json.dump(s, f, indent=2, ensure_ascii=False)
"
rm -f downloads/pending/超大视频.mp4
```

### 18. Steam Workshop 是潜在新来源（未实现）

Steam Workshop (steamcommunity.com/app/431960) 有 Wallpaper Engine 视频壁纸区，curl 可访问，但需要专门的解析脚本。DesktopHut 不可用时这是一个可行的替代来源。

Page 1（首页）已全部发完，但 WallpaperWaifu 各分类 Page 2+ 仍有大量内容：

| 分类 | Page 2 条目数 |
|------|-------------|
| anime | 44 |
| games | 44 |
| fantasy | 44 |
| landscape | 44 |
| lifestyle | 44 |
| movies | 44 |
| pixel-art | 44 |
| music | 0 |
| nature | 0 |

Node 脚本已支持 `--page` 参数，调用示例：
```bash
node scripts/live-wallpaper-download/scripts/download-wallpaperwaifu-first-page.mjs --page 2 --limit 1
```

**Python 脚本目前未使用 `--page` 参数**，只爬 Page 1。扩展方案：循环调用 `anime`、`games`、`fantasy`、`landscape`、`lifestyle`、`movies`、`pixel-art` 等分类页。

### 19. 并发触发导致孤儿文件（⚠️ 重大 bug，需要修复）

**问题**：2026-05-15 晚 23:49–23:52，12 个文件在 3 分钟内全部下载完成，期间发了 30+ 张帖。正常逻辑每次触发只下 1 个。**根因是多个"发动态壁纸"命令并发触发，同时运行了多个 `live_wallpaper_post.py` 实例**。

**后果**：
1. 进程 A 下载 URL1，写入 manifest（status=downloaded）
2. 进程 B 下载 URL2，覆盖写入 manifest（覆盖了进程 A 的内容）
3. 进程 A 的 Python 读 manifest → 读到 URL2 → URL1 的文件存在但 manifest 无记录 → Python 不知道有这个文件 → **孤儿文件**
4. 同时 MoeWalls 的 `loadUrlRecords()` 从 manifest 迁移记录到 dedup 时，**丢失了 `record.status` 字段**（迁移逻辑只迁移 `filePath`），导致 Python 再次因 `record.status=""` 而拒绝接受这些文件

**关键发现**（`loadUrlRecords` 迁移 bug）：

```javascript
// download-moewalls-first-page.mjs 中
const existing = byUrl.get(detailUrl);
if (existing) {
  // ⚠️ 只迁移 filePath，不迁移 status
  item.record = { filePath: existing.filePath };
  // status 字段丢失 → dedup 里 record.status = "" → Python 拒绝
}
```

**两层去重 authority**：

| 数据源 | 权威性 | 用途 |
|--------|--------|------|
| `live_wallpaper_state.json` 的 `posted_detail_urls` | **最高** | Python 层防重复发帖 |
| `downloaded-{source}-detail-urls.json` | 次高 | Node 层防重复下载；`filePath` 字段是指引孤儿文件的关键 |

**临时修复（手动恢复孤儿文件）**：

```python
# 检查 dedup 中的 filePath 是否有文件（dedup 比 manifest 更准确）
import json
from pathlib import Path

dedup = json.loads(Path("config/downloaded-detail-urls.json").read_text())
for entry in dedup:
    if entry.get("filePath"):
        fp = Path(entry["filePath"])
        if fp.exists():
            print(f"ORPHAN FOUND: {fp.name}")  # 文件存在但 manifest 无记录
```

**根本修复方案**（待实现）：
- 添加进程锁：manifest 文件加锁，或用 `flock` 防止并发写入
- 或改为追加写入（append-only manifest），不覆盖
- 或 Python 直接读取 dedup JSON 而不是依赖 manifest

### 20. Manifest 被 `--page` 覆盖时的孤儿文件恢复

当手动或间接用 `--page 2` 运行 WallpaperWaifu 脚本时，**manifest 会被完全覆盖**：Page 1 所有条目（`status=skipped-detail-url`，文件已删除）重新写入 manifest。

但如果 Page 1 有**文件存在但 manifest 中无记录**的条目（可能是之前 `--page 2` 写入后再手动发帖了），该文件的 dedup 记录（`filePath` 字段）仍在 `downloaded-wallpaperwaifu-detail-urls.json` 中。

**恢复步骤**：
```bash
# 1. 从 dedup JSON 中找到文件的 detailUrl 和 filePath
python3 -c "
import json
for x in json.load(open('config/downloaded-wallpaperwaifu-detail-urls.json')):
    if 'Moonlit' in str(x):
        print(x['detailUrl'])
        print(x['filePath'])
"

# 2. 确认文件存在
ls -la "/path/to/file.mp4"

# 3. 手动发帖（用 --video 参数）
tencent-channel-cli feed publish-feed --guild-id 652812504031889164 \
  --channel-id 667049126 --video "/path/to/file.mp4" \
  --title "英文名 · 中文翻译"

# 4. 更新状态并删除文件
python3 -c "
import json
s = json.load(open('live_wallpaper_state.json'))
s['posted_detail_urls'].append('https://wallpaperwaifu.com/.../')
with open('live_wallpaper_state.json','w') as f:
    json.dump(s, f, indent=2, ensure_ascii=False)
"
rm -f "/path/to/file.mp4"
```

**重要**：MoeWalls 的 `downloaded-detail-urls.json` 也需要保持与 `posted_detail_urls` 一致——已发帖的条目才保留，未发帖且无文件的孤儿条目应移除。2026-05-15 已清理 15 条孤儿。

### 21. Steam Workshop 是潜在新来源（未实现）

Steam Workshop (steamcommunity.com/app/431960) 有 Wallpaper Engine 视频壁纸区，curl 可访问，但需要专门的解析脚本。DesktopHut 不可用时这是一个可行的替代来源。

`download-wallpaperwaifu-first-page.mjs` 的 dry-run 模式会把所有条目写入 `manifest-wallpaperwaifu.json`（包括 `filePath` 为空的）。Python 脚本 `_parse_manifest()` 在 dry-run 时包含这些条目，跳过文件存在检查。

### 22. Page 1 全源耗尽里程碑（2026-05-18）

截至 2026-05-18，Page 1 三个源已全部覆盖：
- **WallpaperWaifu Page 1**：22 条壁纸，全部在 `posted_detail_urls` 中，跳过
- **MoeWalls Page 1**：15 条壁纸，全部在 `posted_detail_urls` 中，跳过
- **DesktopHut**：网络超时（12s），服务器不可达

**当前 dedup 规模**：`live_wallpaper_state.json` 记录 `posted_detail_urls` 63 条，`posted_urls` 0 条（视频 URL 单独记录在 dedup 文件）。

**现状**：首页已无新内容，需要扩展到 Page 2+ 或新分类才能继续发帖。WallpaperWaifu 各分类 Page 2 仍有 ~44 条未覆盖（anime/games/fantasy/landscape/lifestyle/movies/pixel-art）。

### 23. 超时后残留锁文件的手动清理

`live_wallpaper_post.py` 有进程锁（`flock`），超时被 kill 后锁文件 `/tmp/live_wallpaper_post.lock` 可能残留（大小 0）。下次运行会报 `Resource deadlock avoided` 并退出。

**症状**：脚本启动后立即退出，log 显示 `[live-wallpaper-post] ===== 动态壁纸发帖开始 @ ...` 后无进一步输出。

**修复**：
```bash
rm -f /tmp/live_wallpaper_post.lock
python3 /root/.hermes/profiles/tencent-channel-live-wallpaper/scripts/live_wallpaper_post.py
```

---

## 频道信息

| 字段 | 值 |
|------|------|
| guild_id | `652812504031889164` |
| channel_id（动态壁纸板块） | `667049126` |
| state 文件 | `/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/live_wallpaper_state.json` |
| 下载目录 | `bdpan-downloads/` |
| Node dedup 文件 | `scripts/live-wallpaper-download/config/downloaded-wallpaperwaifu-detail-urls.json` |

---

## 翻译逻辑

**环境可能没有 `googletrans` 模块**，直接用 curl 调用 Google Translate API：

```bash
curl -s "https://translate.google.com/translate_a/single?client=at&sl=en&tl=zh-CN&dt=t&q=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("TITLE"))')" \
  | python3 -c "import sys,json; data=json.load(sys.stdin); print(''.join(x[0] for x in data[0] if x[0]))"
```

三级 fallback：
1. curl + Google Translate API → 中文
2. MiniMax API → 中文
3. 退回原始英文

---

## Cron 注意事项

hermes-agent cron 有 `inactivity_limit=600s`。如果发帖间隔长（如多段 sleep），可能被判定为 idle 并 kill。

**本次脚本 sleep 极短（curl 下载期间），不受影响。** 但如果后续改动加入长 sleep，需传入 `inactivity_limit=3600`。

---

## 百度网盘（bdpan）配置

**⚠️ 路径注意**：`tencent-channel-dupan-live-wallpaper` profile 包含 bdpan，但 token 存在 `/root/.config/bdpan/`（即 HOME=/root 时）。

| 组件 | 路径 |
|------|------|
| bdpan 二进制 | `/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/home/.local/bin/bdpan` |
| Token 配置 | `/root/.config/bdpan/config.json`（HOME=/root 时读取） |
| Profile HOME | `/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/home` |

**在 hermes-agent 中运行 bdpan 命令**（因为 agent 的 HOME 不是 /root）：
```bash
HOME=/root /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/home/.local/bin/bdpan <cmd>
```

**登录（设备码模式，用户需在百度APP扫码）**：
```bash
export PATH="/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/home/.local/bin:$PATH"
echo "yes" | bdpan login --device-code --accept-disclaimer
```
⚠️ 二维码有效期仅 5 分钟，需在终端直接查看或通过 URL 在浏览器中显示二维码。

---

## bdpan 发帖脚本关键发现（试验得来，不要改）

**主脚本**：`/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/scripts/bdpan_wallpaper_post.py`

### 1. `bdpan download` 必须传完整文件路径，不能传目录

```bash
# ✅ 正确 — 完整远程路径 + 完整本地路径
bdpan download "/apps/bdpan/动态壁纸/其他/武器/Katana.mp4" "/tmp/Katana.mp4" --transfer-dir ""

# ❌ 错误 — 目录作为 local 参数（bdpan 拒绝）
bdpan download "/apps/bdpan/动态壁纸/其他/武器/Katana.mp4" "/tmp/" --transfer-dir ""
```

### 2. `--transfer-dir ""` 防止 bdpan 创建日期子目录

不加 `--transfer-dir ""` 时，bdpan 会把文件下载到 `/tmp/bdpan/2025-01-01/Katana.mp4` 而不是 `/tmp/Katana.mp4`。必须显式传空字符串。

### 3. 先下载到 /tmp，再移动到目标目录

下载到 `bdpan-downloads/` 直接目录时，文件会在下载完成后"消失"（FUSE/网络文件系统同步问题）。正确做法：

```python
tmp_path = f"/tmp/{remote_name}.bdpan"
subprocess.run(["bdpan", "download", remote_path, tmp_path, "--transfer-dir", ""], env=env)
if Path(tmp_path).exists():
    shutil.move(tmp_path, target_dir / remote_name)
```

### 4. `ChannelCLI._env()` 正确写法：先 copy os.environ，显式覆盖 HOME（重要！）

**⚠️ 这个 bug 导致 ffmpeg 找不到**：不要用 `{**dict1, **dict2}` 合并方式来构建 env，这样会把 PATH 清空！

错误写法（PATH 被清空 → ffmpeg/tencent-channel-cli 找不到）：
```python
env = {**os.environ.copy(), "HOME": "/path/to/home"}  # PATH 被清掉！
```

正确写法（保留 PATH）：
```python
def _env(self) -> dict:
    env = os.environ.copy()  # 先复制，保留 PATH
    env["HERMES_HOME"] = str(self.cfg.hermes_home)
    env["HOME"] = str(self.cfg.hermes_home / "home")  # 显式覆盖
    return env
```

### 5. `HERMES_HOME` 可以设置，不影响 bot token

之前担心 `HERMES_HOME` 会干扰视频上传（指向含 `FEISHU_APP_ID` 的 `.env`），实测：只要 `HOME` 指向含 `.qqcli/.env` 的目录，bot token 就能正确读取，`HERMES_HOME` 单独设置不影响。

### 6. bdpan search 分页：只搜到 page 3（~150个文件），之后返回空

`bdpan search "mp4" --page N --page-size 50 --json`：
- Pages 1-3：每次返回 ~50 条，server_mtime 为同一天
- Pages 4+：`has_more=false`，返回空数组
- 总计约 150 个 mp4 文件

### 7. 100MB 文件大小上限（避免下载超时）

搜索结果中 198MB 的大文件下载需 20+ 分钟，极易超时。加 `MAX_SIZE_BYTES` 过滤：

```python
def iter_mp4_newest_first(self, posted_store) -> dict:
    for item in items:
        size = int(item.get("size", 0))
        if size > self.MAX_SIZE_BYTES:
            continue  # 跳过超大文件
        if not posted_store.is_posted(path):
            return item  # 第一个未发帖 → 立即返回
```

**注意**：当前代码中 `MAX_SIZE_BYTES = 300 * 1024 * 1024`（300MB），不是 100MB。Skill 文档中的 100MB 是旧参考值，实际以代码为准。

### 7. Feishu 通知 API：receive_id_type 必须在 URL 参数里

飞书发消息 API 的 `receive_id_type` 必须是 URL query 参数，不是 body 字段：

```python
# ✅ 正确
url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"

# ❌ 错误（HTTP 400）
url = "https://open.feishu.cn/open-apis/im/v1/messages"
body = {"receive_id": "...", "receive_id_type": "open_id", ...}
```

另外 `content` 字段对 text 类型消息需要双重 JSON 序列化：`content = json.dumps({"text": "..."})`。

### 8. 标题格式：`文件名（中文）· 英文翻译`

网盘文件名是中文，从文件名本身提取含义，不需要翻译模块。直接构造标题：

```python
name_zh = Path(baidu_path).stem  # 从路径提取中文文件名
name_en = translator.translate(name_zh)  # Google/MiniMax en→zh
title = f"{name_zh} · {name_en}"
```

### 8. 发帖后删除本地文件并记录路径

```python
if result.success:
    Path(local_file_path).unlink()  # 删除本地视频
    state.mark_bdpan_path_posted(baidu_path)  # 记录已发帖的网盘路径
```

### 9. State 文件新增 `bdpan_posted_paths` 字段

```json
// live_wallpaper_state.json
{
  "posted_detail_urls": [...],      // WallpaperWaifu/MoeWalls 旧来源
  "posted_urls": [...],              // 旧来源视频 URL
  "bdpan_posted_paths": ["/apps/bdpan/动态壁纸/其他/武器/Katana.mp4", ...]  // 新来源
}
```

### 24. bdpan 下载停滞 — /tmp/ 残留 .bdpan 文件导致

**症状**：bdpan download 启动后进度极慢（<100KB/s），下载 30MB 文件在 540KB 处停滞，20+ 分钟无进展，但 `bdpan download` 进程仍在。

**根因**：`/tmp/` 中残留大量历史 .bdpan 文件（约 300MB+，最早从 5月18日），可能是之前下载失败后未清理的临时文件。bdpan 写入时会追加到同名文件，导致文件膨胀/损坏。

**验证方法**：
```bash
ls -la /tmp/*.bdpan
# 正常：只有当前正在下载的文件，大小在持续增长
# 异常：存在大量旧的 .bdpan 文件，大小长期不变
```

**手动测试下载**：
```bash
HOME=/root /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/home/.local/bin/bdpan \
  download "/apps/bdpan/动态壁纸/其他/武器/Sword in forest.mp4" \
  "/tmp/test_sword.mp4.bdpan" --transfer-dir ""
# 观察进度：如果卡在 500KB-1MB 不动，说明 /tmp/ 残留文件在作祟
```

**临时修复**：
1. 杀掉所有 bdpan 进程：`pkill -f "bdpan download"`
2. 删除 `/tmp/` 中的残留 .bdpan 文件（先确认哪些确实已发帖完成）
3. 重新运行下载

**注意**：pending 列表中的 `Kokomi - Starfall.mp4`（128.7MB）和 `Sword in forest.mp4`（30.8MB）下载进度会受此影响，需要在清理残留文件后重新触发。

## Git 分支

`linux` 分支（repo 中已有）。如需 commit，GitHub PAT 在 `~/.netrc` 或 git remote URL 中。
