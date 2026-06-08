视频搬运脚本：/root/.hermes/profiles/tencent-channel-cc/scripts/mengde_video_poster.py
- 从抖音/小红书/快手分享文案下载无水印视频，随机轮询发到5个频道主频道（自拍摄影圈/孟德严选/女友控/忏悔一切/肉腿控），发完从池移除，池空重置打乱
- 依赖 remove-short-videos-watermark（已 clone 到同目录）
- 用户要求：抖音用 native 模式（无 cookie），只接受视频链接
- **运行方式：CLI 模式 `python scripts/mengde_video_poster.py "分享文案"`（推荐），stdin JSON 有 bug不要用**
- **提取链接：从分享文案中提取 URL，脚本内自动识别平台，文件名含平台关键词则发纯视频**
- 发帖成功判断：检查返回内容含 "pd.qq.com"，不含则抛异常
- 不要每次都问用户确认，脚本自动选频道直接发
- tencent-channel-cc 只保留这个轮换发帖功能；发壁纸相关脚本已删除，不能再发壁纸
- 账号轮换：默认账号显示为“怪异星人”，额外账号“怪异仙人”放在 `home/.qqcli/account_tokens.json`；账号轮询状态文件 `/tmp/mengde_account_round_robin.json`
