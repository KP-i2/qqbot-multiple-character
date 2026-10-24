"""
消息匹配规则（@me、命令识别、白名单群自动回复）
"""
import nonebot
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, PrivateMessageEvent

from . import config as cfg


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
    """消息触发规则（@ 才回）:
    1. bot 自己的消息 → 忽略（防止 bot 自言自语、角色扮演嘴瓢触发回复）
    2. 角色级访问控制 → 受限角色只对白名单用户响应（2026-09-10 新增）
    3. 私聊消息 → 一律放行（命令是否可执行由 handle_chat 内的 admin 校验决定）
    4. 群消息 @bot → 总是触发
    5. 群消息 未@bot 但以 / 或 ／ 开头（命令） → 强制触发（让 admin 无 @ 时也能用命令）
    6. 其他群消息 → 忽略（不再有白名单群自动回复）

    命令是否真的能执行由 handle_chat 里的 admin 校验决定，规则层只决定消息能否进入 handler。
    """
    try:
        bot = nonebot.get_bot()
        # 过滤掉 bot 自己发出去的消息（避免自对话 + 测试模式拦截 bot 自己）
        if str(event.get_user_id()) == str(bot.self_id):
            logger.debug(f"[self] bot 自己的消息忽略: {event.get_user_id()}")
            return False
    except Exception:
        pass

    # 2026-09-10: 角色级访问控制。放在最前面拦，非白名单用户的消息
    # 既不进 handler、也不触发"唔…让窝想想"提示、更没有 LLM 调用开销。
    if not _sender_allowed(event):
        return False

    if isinstance(event, PrivateMessageEvent):
        # 私聊一律放行。命令 / 切角色 / 改设置等受限操作在 handle_chat / commands 里走 admin 校验。
        return True
    if isinstance(event, GroupMessageEvent):
        try:
            bot = nonebot.get_bot()
            if is_at_me(event, bot):
                return True
            # 命令前缀 → 强制触发（让 admin 无 @ 时也能稳定使用命令）
            if _looks_like_command(event):
                return True
            # 其他群消息一律忽略（@ 才回，不再做白名单自动回复）
            logger.debug(
                f"Group msg ignored (no @): group={event.group_id} "
                f"user={event.get_user_id()} to_me={getattr(event, 'to_me', None)}"
            )
            return False
        except Exception as e:
            logger.error(f"respond_rule error: {e}")
            return False
    return False


def _sender_allowed(event: Event) -> bool:
    """角色级访问控制：当前激活角色若声明了 allowed_users，则只放行白名单用户。

    2026-09-10 新增，配合 dot_yuu 这类 private 角色（SKILL.md 里写
    allowed_users: 2972494435）。未声明该字段的角色不受影响。

    失败时**保守拒绝**：这个闸门保护的是受限角色的内容外泄，
    宁可让 bot 暂时不吭声（日志里能看到 [access] 报错），也不能漏放。
    若当前没有加载任何角色（skill 为 None），说明没有需要保护的对象，放行。
    """
    try:
        # 函数内 import：避免 rules → skill_manager/history 的模块级循环依赖
        from . import history as hist
        from . import skill_manager

        active = hist.global_active_skill or skill_manager.DEFAULT_SKILL_NAME or ""
        skill = skill_manager.ALL_SKILLS.get(active)
        if skill is None or not skill.allowed_users:
            return True  # 未加载 / 无限制 → 所有人可用
        uid = str(event.get_user_id())
        if uid in skill.allowed_users:
            return True
        logger.info(
            f"[access] 角色 '{active}' 仅限 {sorted(skill.allowed_users)}，"
            f"已拦截 user={uid} group={getattr(event, 'group_id', '-')}"
        )
        return False
    except Exception as e:
        logger.error(f"[access] 访问控制检查异常，保守拒绝本次消息: {type(e).__name__}: {e}")
        return False


def _looks_like_command(event: GroupMessageEvent) -> bool:
    """检查消息文本是否以命令前缀开头（/ 或 ／），用于在 respond_rule 中提前放行命令"""
    text = event.get_plaintext().strip()
    if not text:
        return False
    return text.startswith("/") or text.startswith("／")
