腾讯频道壁纸机器人（tencent-channel profile）：
- 频道：Wallpaper壁纸库，静态壁纸板块（channel_id=669891684，guild_id=652812504031889164）
- 手动发帖脚本：`wallpaper_post_v2.py`（触发词"Wallpaper壁纸库发帖"），内部调用 `wallpaper_cold_detect_v2.1.py --force`，跳过冷度检测直接发帖
- 冷度检测脚本：`wallpaper_cold_detect_v2.1.py`（Cron: 0 7-23 * * *），含 file_paths 修复和 feed_id 自发帖识别
- 翻译：Google Translate 优先 + MiniMax API 候补 + 退回英文（三级fallback）
- Tags 缓存：`wallpapers/.tags_cache.json`（wallhaven_id → {tags, resolution}）
- 发帖：手动用 CLI args 模式（--image flag），cron 用 stdin JSON 模式（file_paths）
- 下载目录：`/root/.hermes/profiles/tencent-channel-june/media`
- Token 配置：必须是完整格式 `bot:v1_xxx`，不带 `bot:` 前缀会导致 retCode=100007 鉴权失败
- 用户偏好：问题一次性编号给我，我会逐个修复并解释；期望改动后能验证语法和实际运行效果
§
触发词"Wallpaper壁纸库发帖" → 执行 /root/.hermes/profiles/tencent-channel-june/scripts/wallpaper_post_v2.py
§
hermes-agent session 的 `~`（即 Path.home()）可能不等于用户 shell 的 `~`。当前 session 的 HOME 是 `/root/.hermes/hermes-agent`，而用户的 ~ 是 `/root`。clone 或 cp 文件时若用 `~/.hermes/...` 路径，文件会到不同位置。解决：用 `echo $HOME` 和 `pwd` 交叉验证，必要时用绝对路径 `/root/.hermes/...`。
§
动态壁纸发帖脚本 live_wallpaper_post.py：
- 路径：/root/.hermes/profiles/tencent-channel-live-wallpaper/scripts/live_wallpaper_post.py
- 目标：guild_id=652812504031889164, channel_id=667049126（动态壁纸板块）
- 翻译复用 wallpaper_cold_detect_v2.1.py 的 Google+MiniMax 逻辑
- 下载源：轮询 WallpaperWaifu→MoeWalls→DesktopHut（live-wallpaper-download/scripts/）
- state文件：/root/.hermes/profiles/tencent-channel-live-wallpaper/live_wallpaper_state.json
- 发帖后删除视频文件，URL记入state文件防重复发帖
§
动态壁纸Bot（2026-05-16 并发修复）：
- 进程锁：fcntl.flock + SIGTERM/SIGINT 处理，LOCK_PATH=/tmp/live_wallpaper_post.lock
- State 持久化：mark_posted() 每次发帖后同步写回 live_wallpaper_state.json
- _recover_from_dedup()：启动时扫描3个dedup文件，把"文件已不存在"的条目恢复进 posted_urls（恢复了63个历史记录）
- loadUrlRecords()：MoeWalls/WallpaperWaifu/DesktopHut 三个脚本都加了 status 字段迁移
- 12个孤儿文件保留在downloads/，不会重发
- 触发词"发动态壁纸" → 直接说结果，不用详细日志