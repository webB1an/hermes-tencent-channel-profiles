当前 Agent 主要职责（tencent-channel-sing）：
- 只负责「语音翻唱聊天聊天社区 > 唱歌/翻唱板块」（guild_id=585169334083036916, channel_id=732731676）
- 视频搬运脚本：/root/.hermes/profiles/tencent-channel-sing/scripts/sing_video_poster.py
- Token：~/.hermes/profiles/tencent-channel-sing/home/.qqcli/.env（QQ_AI_CONNECT_TOKEN）
- 用户操作习惯：直接给抖音分享文案 → 我执行脚本全自动发帖，**不需要任何确认或询问**，直接发
- 注意：tencent-channel-sing 的 .env 没有 TOKEN 字段，TOKEN 在 home/.qqcli/.env 里
- 其他 profile（tencent-channel / tencent-channel-cc / tencent-channel-june 等）均不属于当前 Agent 职责范围