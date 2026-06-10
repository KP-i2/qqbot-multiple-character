"""
消息匹配规则（@me、命令识别）
"""

import nonebot
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, PrivateMessageEvent


def is_at_me(event: GroupMessageEvent, bot: Bot) -> bool:
    # 优先使用 NoneBot2 内置的 to_me 属性
    if getattr(event, 'to_me', False):
        return True
    # 兜底：手动检查 at 段
    self_id = str(bot.self_id)
    for seg in event.message:
        if seg.type == "at" and str(seg.data.get("qq", "")) == self_id:
            return True
    return False


def is_command(text: str) -> bool:
    """检查是否是命令"""
    prefixes = ["/", "／"]
    commands = ["reset", "重置", "清空记忆", "skills", "角色", "列表",
                 "switch", "切换", "current", "当前", "zyw", "人设"]
    for p in prefixes:
        if text.startswith(p):
            cmd = text[len(p):].strip().split()[0] if text[len(p):].strip() else ""
            if cmd in commands:
                return True
    return False


async def respond_rule(event: Event) -> bool:
    logger.info(f"[RULE] Event type={type(event).__name__}, user={event.get_user_id()}")
    if isinstance(event, PrivateMessageEvent):
        return True
    if isinstance(event, GroupMessageEvent):
        try:
            bot = nonebot.get_bot()
            result = is_at_me(event, bot)
            if not result:
                logger.debug(
                    f"Group msg ignored: to_me={getattr(event, 'to_me', None)}, "
                    f"self_id={bot.self_id}, segments={[(s.type, s.data) for s in event.message]}"
                )
            return result
        except Exception as e:
            logger.error(f"respond_rule error: {e}")
            return False
    return False
