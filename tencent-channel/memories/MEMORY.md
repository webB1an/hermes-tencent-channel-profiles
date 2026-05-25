腾讯频道壁纸机器人（tencent-channel profile）：
- 频道：Wallpaper壁纸库，静态壁纸板块（channel_id=669891684，guild_id=652812504031889164）
- 手动发帖脚本：`wallpaper_post_v2.py`（触发词"Wallpaper壁纸库发帖"），内部调用 `wallpaper_cold_detect_v2.1.py --force`，跳过冷度检测直接发帖
- 冷度检测脚本：`wallpaper_cold_detect_v2.1.py`（Cron: 0 7-23 * * *），含 file_paths 修复和 feed_id 自发帖识别
- 翻译：Google Translate 优先 + MiniMax API 候补 + 退回英文（三级fallback）
- Tags 缓存：`wallpapers/.tags_cache.json`（wallhaven_id → {tags, resolution}）
- 发帖：手动用 CLI args 模式（--image flag），cron 用 stdin JSON 模式（file_paths）
- 下载目录：`/root/.hermes/profiles/tencent-channel/media`
- 用户偏好：问题一次性编号给我，我会逐个修复并解释；期望改动后能验证语法和实际运行效果
§
腾讯频道壁纸机器人配置：
- 频道：Wallpaper壁纸库，静态壁纸板块（channel_id=669891684，guild_id=652812504031889164）
- 手动发帖：wallpaper_post_v2.py（触发词"Wallpaper壁纸库发帖"）
- 冷度检测：wallpaper_cold_detect_v2.1.py（Cron: 0 7-23 * * *）
- 翻译：Google Translate 优先 + MiniMax API 候补 + 退回英文
- Tags 缓存：wallpapers/.tags_cache.json（wallhaven_id → {tags, resolution}）
- 发帖方式：手动用 CLI args 模式（--image flag），cron 用 stdin JSON 模式（file_paths）
- 下载目录：/root/.hermes/profiles/tencent-channel/media
- 用户偏好：问题一次性编号；改动后验证语法和实际运行效果；消息UI有时会看不到路径/内容（重发可解决）
- 触发词靠记忆处理，用户期望改用 skill 方式更稳定
§
anime-pictures.net 壁纸机器人：
- 主页"今日最佳"/"本周最佳"是 JS 动态渲染，requests HTML 里只有 spinner。必须用 browser_console JS 提取：querySelectorAll('.index_page') 找含 "Highest rated" 的 title，取 a[href*="/posts/"] 的链接
- 完整图片 CDN（images/api.anime-pictures.net）不通，opreviews CDN 的 _bp.avif 预览图（~24KB）可用
- 标签解析：从帖子页 <script type="application/json"> 的 __NUXT_DATA__ JSON 提取，type:1=角色,2=参考,3/6=版权方,4=画师
- anime_pictures_post.py（手动触发脚本）：Config 直接实例化不用 .load()；HttpClient(user_agent, timeout)；ImageDownloader.download() 返回 (bytes, src_desc, fmt)
- 状态文件：anime_pictures_state.json（posted_ids 防重）
- Skill：automation/anime-pictures-posting