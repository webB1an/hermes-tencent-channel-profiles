---
name: wallpaper-bot
description: Wallpaper壁纸库自动发帖机器人 + Wallhaven壁纸库分类分发 + 煎蛋树洞同步。冷度检测发帖（v2.1/v2.2）、手动强制发帖、Wallhaven 多板块分类分发、煎蛋树洞→废话回收站同步（内容分类+防重）。包含 file_paths 修复、feed_id 自发帖识别、指数退避重试、posted_ids.json 去重等关键实现细节。
---

# Wallpaper Bot 实现方案

## 目录结构

```
~/.hermes/profiles/tencent-channel/scripts/
├── wallpaper_cold_detect_v2.1.py   # Wallpaper壁纸库 cron 定时任务主脚本（冷度检测发帖）
├── wallpaper_post_v2.py             # Wallpaper壁纸库手动触发 wrapper（调用 v2.1 --force）
├── wallhaven_cold_detect_v2.2.py   # Wallhaven壁纸库 cron 定时任务主脚本（分类分发版）
├── wallhaven_post_v2.py            # Wallhaven壁纸库手动触发 wrapper（调用 v2.2 --force）
├── wallhaven_translate.py           # Wallhaven 标签翻译模块（Google+M iniMax 链路）
├── sync_jandan_treehole.py          # 煎蛋树洞→废话回收站同步脚本（cron 30min）
└── cleanup_media.py                # 每日媒体清理
```

## 频道矩阵

| 频道 | GUILD_ID | 用途 | 板块 |
|------|----------|------|------|
| Wallpaper壁纸库 | `652812504031889164` | 旧频道，静态壁纸 | `669891684`（静态壁纸） |
| **Wallhaven壁纸库** | `640973304078348133` | 新频道，分类分发 | 多板块（见下） |

## Wallhaven 壁纸库板块映射

| 分类 Key | 板块名称 | CHANNEL_ID |
|----------|----------|------------|
| anime | 动漫插画 | `728523265` |
| game | 游戏电竞 | `728523307` |
| landscape | 风景自然 | `728523284` |
| car | 汽车机械 | `728523386` |
| city | 城市建筑 | `728523356` |
| scifi | 奇幻科幻 | `728523341` |
| animal | 动物萌宠 | `728523374` |
| people | 人物摄影 | `728523320` |
| art | 抽象艺术 | `728523340` |
| movie | 影视音乐 | `728523419` |
| solid | 简约纯色 | `728523356` |
| default | 全部 | `728513789` |

## Wallpaper壁纸库 核心参数

| 参数 | 值 |
|------|-----|
| 频道 GUILD_ID | `652812504031889164` |
| 静态壁纸版块 CHANNEL_ID | `669891684` |
| 壁纸来源 | Wallhaven `/search?sorting=random&purity=100` |
| 每帖图片数 | 3 |
| 最小自发帖间隔 | 120 分钟 |
| 冷度阈值 | 65 分钟无真人新帖 |
| 下载目录 | `/root/.hermes/profiles/tencent-channel/media` |

## Wallhaven壁纸库 核心参数

| 参数 | 值 |
|------|-----|
| 频道 GUILD_ID | `640973304078348133` |
| 默认板块 CHANNEL_ID | `728523265`（动漫插画） |
| 发帖模式 | `classify`（分类分发）/ `merge`（合并单帖） |
| 壁纸来源 | Wallhaven `/search?sorting=random&purity=100` |
| 每轮候选数 | 3 |
| 最小自发帖间隔 | 120 分钟 |
| 冷度阈值 | 65 分钟无真人新帖 |
| 下载目录 | `/root/.hermes/profiles/tencent-channel/media/wallhaven` |

## 关键实现细节

### 1. CLI stdin JSON 模式图片字段（重要坑）

**正确：**
```python
payload = {
    "file_paths": [{"file_path": "/abs/path/to/image.jpg"} for p in image_paths],
}
```

**错误（静默忽略图片）：**
```python
payload = {"images": [str(p) for p in image_paths]}  # ← 错！
```

CLI flag 模式用 `--image /path --image /path`，与 stdin JSON 不兼容，不能混用。

### 2. 自发帖识别

从 `get-guild-feeds` 获取帖子后，判断是否为脚本自发帖：
- 优先：用 `feed_id` 精确匹配 `store.self_feed_ids`
- 兜底：时间戳 ±90 秒内匹配 `store.self_post_timestamps`

### 3. 去重机制

`wallpaper_state.json` 持久化：
- `posted_ids`：已发过的 wallhaven ID（保留最近 1000 条）
- `self_feed_ids`：自发帖 feed_id（保留最近 200 条）
- `self_post_timestamps`：自发帖时间戳（用于时间兜底）
- `last_self_post_ts`：最近一次自发帖时间（用于最小间隔判断）

### 4. Wallhaven 请求优化

- 先从 `random` 接口批量获取候选（每页 24 条，最多 6 页）
- **只对最终候选**（最多 3 张）单独请求 `/w/{id}` 获取 tags
- 已有本地缓存文件的壁纸，跳过下载并从本地缓存读取 tags

### 5. 翻译链路

Google Translate 优先 → MiniMax API 候补 → 退回英文原文

### 6. cron 调度

| job_id | 任务 | 脚本 | Schedule | 备注 |
|--------|------|------|----------|------|
| `1a27802e2c61` | Wallpaper壁纸库冷度检测 | `wallpaper_cold_detect_v2.1.py` | `0 7-23 * * *` | 整点执行 |
| `6a9594fb7bdf` | **Wallhaven壁纸库冷度检测** | `wallhaven_cold_detect_v2.2.py` | `5 7-23 * * *` | **建议错开 5 分钟** |
| `ca13f6909bd9` | 清理media目录 | `cleanup_media.py` | `0 3 * * *` | 每日凌晨 |
| `08ca34cb4bb4` | 煎蛋树洞同步（废话回收站） | `sync_jandan_treehole.py` | `30 * * * *` | 已暂停 |

> ⚠️ **撞车风险**：Wallpaper壁纸库和 Wallhaven壁纸库如果都配 `0 7-23 * * *` 会同时跑，争抢 `tencent-channel-cli` 资源。建议 Wallhaven 错开 5 分钟（`5 7-23 * * *`）。

## 煎蛋树洞同步（废话回收站）

脚本：`sync_jandan_treehole.py`

每 30 分钟抓取煎蛋网树洞（post_id=102312）新评论，按内容分类发到废话回收站（guild_id=622213584078628209）对应板块。

**板块分类规则：**

| 板块 | 触发条件 |
|------|---------|
| 有毒废物 | 含负能量词（傻/笨/emo/焦虑/崩溃/想死/气死了等） |
| 选择困难 | 含决策词（要不要/怎么办/纠结/选A还是B/救命等） |
| 摸鱼回收 | 含工作词（上班/加班/工资/老板/辞职/offer等） |
| 垃圾影展 | 评论带图片 |
| 分拣中心 | 有子评论（sub_comment_count > 0） |
| 可回收物 | 含正能量词（谢谢/哈哈/开心/加油/棒/赞等） |
| 夜间投放 | 22:00-03:00 发 |
| 匿名丢弃 | 默认 |

**防重：** `state/.jandan_synced_ids.json` 记录已同步 ID（成功=-1，失败=重试计数）
**过滤：** 含敏感词（性侵/http/煎蛋/方丈/do/蛋友）直接跳过
**发帖：** `tencent-channel-cli feed publish-feed`，stdin JSON 模式，`file_paths=[]`
**重试：** 失败最多 3 次，随机间隔 5~15s；发完随机等 **10~30s** 防频率过快（已优化 2026-04-17）
**cron deliver：** `origin`（结果回传到当前聊天）

## 验证命令

```bash
# Wallpaper壁纸库 dry-run
HERMES_HOME=/root/.hermes/profiles/tencent-channel \
  python3 scripts/wallpaper_cold_detect_v2.1.py --dry-run -v

# Wallpaper壁纸库 强制发帖
HERMES_HOME=/root/.hermes/profiles/tencent-channel \
  python3 scripts/wallpaper_cold_detect_v2.1.py --force -v

# Wallhaven壁纸库 dry-run（分类分发模式）
HERMES_HOME=/root/.hermes/profiles/tencent-channel \
  python3 scripts/wallhaven_cold_detect_v2.2.py --dry-run -v

# Wallhaven壁纸库 强制发帖（分类分发）
HERMES_HOME=/root/.hermes/profiles/tencent-channel \
  python3 scripts/wallhaven_cold_detect_v2.2.py --force -v

# Wallhaven壁纸库 强制发帖（合并模式 --merge）
HERMES_HOME=/root/.hermes/profiles/tencent-channel \
  python3 scripts/wallhaven_cold_detect_v2.2.py --force --merge -v
```

## 调试技巧

### cron 任务 "error" 状态排查

`last_status: error` 可能是**误报**——cron runner 超时杀掉进程时返回 exit=124，状态机标记为 error，但脚本本身并未出错。

**诊断步骤：**

```bash
# 1. 手动跑脚本，加 timeout 观察输出（先 10s 看前导日志，再加长）
HERMES_HOME=/root/.hermes/profiles/tencent-channel timeout 10 python3 scripts/sync_jandan_treehole.py -v 2>&1

# 2. 用 strace 确认网络请求是否卡住（无输出=请求正常返回）
HERMES_HOME=/root/.hermes/profiles/tencent-channel timeout 8 strace -e trace=network -f python3 scripts/sync_jandan_treehole.py 2>&1 | tail -20

# 3. 找 cron 输出日志
find /root/.hermes/profiles/tencent-channel/state -name "*.json" 2>/dev/null
```

### 煎蛋树洞同步慢/超时问题（已修复 2026-04-17）

**现象：** `last_status: error` 但 API 和发帖都正常
**根因：** 两个 bug 同时存在：
1. `tencent-channel-cli feed publish-feed` **成功时也返回 exit code 1**（非标准行为，实测确认）。脚本误把成功当失败，发完继续循环等待重试，sleep 60~300s 累积超时。
2. cron deliver=`local` 时 runner 只等超时退出，被 SIGTERM 杀掉后**进度没保存**（synced_ids、cutoff 都停在上一轮），下次跑会重复发帖。

**修复（已生效）：**
1. sleep 降至 **10~30s**（30 分钟 cron 周期完全够用）
2. **每条发完立即保存** checkpoint（`save_last_sync` + `save_synced_ids`），不怕被 SIGTERM 杀
3. **exit(1) → exit(0)**（成功后直接退出，不重试）
4. cron deliver 改为 **`origin`**，下次运行完结果回传到聊天，不再报 error

**已知工作区 cron job_id:**
- `1a27802e2c61`: Wallpaper壁纸库冷度检测（`0 7-23 * * *`）
- `6a9594fb7bdf`: Wallhaven壁纸库冷度检测（`5 7-23 * * *`，2026-05-11 新建）
- `08ca34cb4bb4`: jandan 树洞同步（`30 * * * *`，已暂停）
- `ca13f6909bd9`: 清理media目录（`0 3 * * *`）

**验证 checkpoint 正常推进：**
```bash
HERMES_HOME=/root/.hermes/profiles/tencent-channel python3 -c "
import sys; sys.path.insert(0, 'scripts')
from sync_jandan_treehole import Config, load_synced_ids, load_last_sync
cfg = Config.load()
print('synced count:', len(load_synced_ids(cfg.state_file)))
print('cutoff ts:', load_last_sync(cfg.last_sync_file, cfg))
"
```

**验证：**
```bash
# 验证 checkpoint 正常推进
HERMES_HOME=/root/.hermes/profiles/tencent-channel python3 -c "
import sys; sys.path.insert(0, 'scripts')
from sync_jandan_treehole import Config, load_synced_ids, load_last_sync
cfg = Config.load()
print('synced:', len(load_synced_ids(cfg.state_file)))
print('cutoff:', load_last_sync(cfg.last_sync_file, cfg))
"
```

### sessions/ 和 cron/output/ 清理（已升级）

`cleanup_media.py` 已升级为每日全量清理脚本（cron `0 3 * * *`）：

| 目录 | 清理策略 |
|------|---------|
| `media/` | 全量删除图片（发帖后即清） |
| `sessions/` | 保留 7 天（`.json` / `.jsonl`） |
| `cron/output/` | 保留 7 天（`.md`） |
| `state/` | **不清理**（wallpaper_state、wallhaven_state、jandan_synced_ids 等含去重进度，删除会重复发帖） |

```bash
# 验证清理效果
python3 scripts/cleanup_media.py
```

### .gitignore

项目根目录已创建 `.gitignore`：

```
media/                        # 图片缓存
sessions/                     # Session + jsonl + request_dump
cron/output/                  # Cron 输出
logs/                         # 日志
state.db / state.db-*          # SQLite WAL
wallpaper_state.json / wallhaven_state.json  # 持久化状态
models_dev_cache.json / processes.json / feishu_seen_message_ids.json
channel_directory.json / .skills_prompt_snapshot.json
scripts/__pycache__/          # Python 缓存
```

**注意**：
- .gitignore 只对**未跟踪的文件**生效。已 track 的文件（如 `channel_directory.json`、早先 commit 的 `media/`）不受影响，运行时会正常显示 `M` / `D`，无需处理。
- 初次创建 .gitignore 后，之前 commit 过的 media 文件被 cleanup 脚本删除，会显示为 `D`（索引中存在但文件已删除）。一次性清理：`git ls-files --deleted | xargs git rm --cached` → `git commit -m "remove old tracked media files from index"`。

## 更新记录

- 2026-05-11: 发现 Wallhaven壁纸库 cron 任务从未创建（用户误以为存在）；新建 `6a9594fb7bdf`；补充 schedule 撞车风险说明
- v2.2: 新增 Wallhaven 壁纸库（guild_id=640973304078348133），支持按 tag 分类分发到多板块
- v2.1: `images` → `file_paths` 修复；feed_id 精确识别；指数退避重试；posted_ids 去重
- 煎蛋树洞同步: sleep 60~300s→10~30s；每条发完立即 checkpoint；cron deliver→origin
- cleanup_media.py 升级: media 全量清理 + sessions/ + cron/output/ 保留 7 天；新增 .gitignore
