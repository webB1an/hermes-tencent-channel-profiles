"""Suppress duplicate agent success replies for tencent-channel-cc.

The posting script sends the canonical Feishu notification directly through
Open API. Gateway's automatic final response would otherwise create a second
success message.
"""

import logging
import re

logger = logging.getLogger("hooks.suppress-agent-post-success")

CHANNEL_NAMES = ("自拍摄影圈", "孟德严选", "女友控", "忏悔一切", "肉腿控")


def _is_agent_post_success(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if "发帖失败" in text or "错误" in text:
        return False
    if "帖子链接：" in text and "账号：" in text and "频道：" in text:
        return True
    if "已发至" in text and any(name in text for name in CHANNEL_NAMES):
        return True
    if re.search(r"发帖完成[！!\\s\\S]{0,120}(发帖频道|发帖链接|发帖结果)", text):
        return True
    return False


async def handle(event_type: str, context: dict) -> None:
    if event_type != "gateway:startup":
        return

    from gateway.platforms.base import SendResult
    from gateway.platforms.feishu import FeishuAdapter

    if getattr(FeishuAdapter, "_tencent_cc_post_success_suppressed", False):
        return

    original_send = FeishuAdapter.send

    async def wrapped_send(self, chat_id, content, reply_to=None, metadata=None):
        if _is_agent_post_success(content):
            logger.info("Suppressed duplicate agent post success reply to %s", chat_id)
            return SendResult(success=True, message_id=None)
        return await original_send(self, chat_id, content, reply_to=reply_to, metadata=metadata)

    FeishuAdapter.send = wrapped_send
    FeishuAdapter._tencent_cc_post_success_suppressed = True
    logger.info("Installed duplicate post success suppression for Feishu replies")
