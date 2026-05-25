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
视频搬运脚本：/root/.hermes/profiles/tencent-channel-cc/scripts/mengde_video_poster.py
- 从抖音/小红书/快手分享文案下载无水印视频，随机轮询发到5个频道主频道（自拍摄影圈/孟德严选/女友控/忏悔一切/肉腿控），发完从池移除，池空重置打乱
- 依赖 remove-short-videos-watermark（已 clone 到同目录）
- 用户要求：抖音用 native 模式（无 cookie），只接受视频链接
- 文案逻辑：文件名去 # 后缀；文件名含 douyin/dy/xiaohongshu/xhs/kuaishou/ks 则发纯视频
- 轮询状态文件：~/.tmp/mengde_round_robin.json
- CLI 模式：`python mengde_video_poster.py "分享文案"`
- stdin 模式有 bug（json 空输入报错），优先用 CLI 模式
- 重要：用户要求全自动随机轮询，**不需要任何交互选择**，脚本应自己选好频道直接发帖
§
视频搬运脚本：/root/.hermes/profiles/tencent-channel-cc/scripts/mengde_video_poster.py
- 从抖音/小红书/快手分享文案下载无水印视频，随机轮询发到5个频道主频道（自拍摄影圈/孟德严选/女友控/忏悔一切/肉腿控），发完从池移除，池空重置打乱
- 依赖 remove-short-videos-watermark（已 clone 到同目录）
- 用户要求：抖音用 native 模式（无 cookie），只接受视频链接
- **运行方式：CLI 模式 `python scripts/mengde_video_poster.py "分享文案"`（推荐），stdin JSON 有 bug不要用**
- **提取链接：从分享文案中提取 URL，脚本内自动识别平台，文件名含平台关键词则发纯视频**
- 发帖成功判断：检查返回内容含 "pd.qq.com"，不含则抛异常
- 不要每次都问用户确认，脚本自动选频道直接发