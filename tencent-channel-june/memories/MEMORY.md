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