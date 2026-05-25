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
bdpan 动态壁纸脚本：scripts/bdpan_wallpaper_post.py（tencent-channel-dupan-live-wallpaper profile）
  下载必须用 background=true 模式（terminal 600s hard timeout，大文件必然超时）
  下载目标：bdpan-downloads/
  搜索：bdpan search "mp4" --page N --json，流式找第一个未发帖即返回
  下载：bdpan-downloads/ 内生成 fs_id 前缀的唯一文件名，*.bdpan 临时文件也保留在该目录
  ChannelCLI._env()：HOME=hermes_home/home（含 bot token），HERMES_HOME 显式设
