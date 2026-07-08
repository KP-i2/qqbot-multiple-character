"""
命令处理函数（被 chat_handler.py 的 text-based dispatch 调用）

注意：本模块不注册 on_command，避免与 chat_handler.py 的群聊 @bot
命令分发重复注册 TrieRule（消除启动时 Duplicated prefix 警告）。
所有命令由 chat_handler.py 统一调度，私聊和群聊 @bot 都能用。

权限策略：所有命令仅管理员可用（user 期望：管理员能用指令）。
白名单群 / 命令的强制触发在 rules.respond_rule 里处理（绕开 35% 概率）。
"""
import asyncio
import json

from nonebot.adapters.onebot.v11 import Bot, Event

from . import config as cfg
from . import history as hist
from . import skill_manager
from . import avatar
from . import emoji_system
from . import api_client


# ============================================================
# 命令实现函数（从 chat_handler.py 提取出来，方便维护）
# 每个函数返回 (handled: bool, response_text: str)，调用方决定是否 finish
# 所有命令均已加 admin 校验；非管理员直接返回统一提示
# ============================================================

_NOT_ADMIN_MSG = "欸——这个只有窝自己才能操作哦 🙈 对不起嘛！"


def _require_admin(event: Event) -> tuple[bool, str] | None:
    """返回 None 表示是管理员；返回 (True, msg) 表示拒绝"""
    if not cfg.is_admin(event.get_user_id()):
        return True, _NOT_ADMIN_MSG
    return None


def cmd_reset(event: Event) -> tuple[bool, str]:
    """清空对话历史（仅管理员）"""
    if (r := _require_admin(event)):
        return r
    key = hist.get_history_key(event)
    hist.clear_history(key)
    return True, "好叭！窝把之前聊的都忘光光啦 (◍•ᴗ•◍)❤ 重新开始叭～"


def cmd_skills(event: Event) -> tuple[bool, str]:
    """列出所有技能（仅管理员）"""
    if (r := _require_admin(event)):
        return r
    active = hist.global_active_skill or skill_manager.DEFAULT_SKILL_NAME or ""
    if not skill_manager.ALL_SKILLS:
        return True, "呜哇！现在窝这里一个角色都没有加载捏 [委屈]"
    lines = ["泥可以选这些角色陪泥聊天哦：\n"]
    for name, skill in skill_manager.ALL_SKILLS.items():
        marker = " 👈 当前" if name == active else ""
        desc = skill.description or "暂无描述"
        ver = f" v{skill.version}" if skill.version else ""
        lines.append(f"  {skill.display_name}({name}){ver} — {desc}{marker}")
    lines.append(f"\n发送 /switch <名字> 就能换人啦～")
    return True, "\n".join(lines)


def cmd_switch(bot: Bot, event: Event, arg: str) -> tuple[bool, str]:
    """切换角色（仅管理员）"""
    if (r := _require_admin(event)):
        return r
    if not arg:
        names = ", ".join(f"{s.display_name}({n})" for n, s in skill_manager.ALL_SKILLS.items())
        return True, f"要用这个格式哦：/switch <角色名> ✨\n可选的有：{names} 💕"
    if arg not in skill_manager.ALL_SKILLS:
        names = ", ".join(f"{s.display_name}({n})" for n, s in skill_manager.ALL_SKILLS.items())
        return True, f"呜呜 '{arg}' 这个角色窝找不到捏 [抓狂]\n这些是窝有的：{names} 泥再挑挑？"

    hist.global_active_skill = arg
    _safe_create_task(avatar.set_profile(bot, arg), f"profile-{arg}")
    skill = skill_manager.ALL_SKILLS[arg]
    return True, (
        f"切换成功惹！现在是 {skill.display_name}({arg}) 在陪泥啦 🧡🌟\n"
        f"{skill.description or ''}\n"
        f"之后大家聊天都会用这个角色辽～"
    )


def cmd_current(event: Event) -> tuple[bool, str]:
    """显示当前角色（仅管理员）"""
    if (r := _require_admin(event)):
        return r
    from . import provider as prov
    active = hist.global_active_skill or skill_manager.DEFAULT_SKILL_NAME or ""
    skill = skill_manager.ALL_SKILLS.get(active)
    if not skill:
        return True, "窝还没有给泥准备可以聊天的角色呢 [对手指] 泥想选谁呀？"
    p = prov.get_active_provider()
    return True, (
        f"听好惹！现在是 {skill.display_name}({skill.name}) 在陪泥聊天哟💕\n"
        f"{skill.description or '暂无描述'}\n"
        f"版本：{skill.version or '?'} 📱✨\n"
        f"模型：{p['model']} ({p['name']}) 🧠💭"
    )


def cmd_reloademoji(event: Event) -> tuple[bool, str]:
    """重载表情（仅管理员）"""
    if (r := _require_admin(event)):
        return r
    emoji_system.load_emoji_files()
    total_imgs = sum(len(v) for v in emoji_system.EMOJI_FILES.values())
    emotions = list(emoji_system.EMOJI_FILES.keys())
    lines = [f"叮咚～窝重新整理了一下表情包包！现在有 {total_imgs} 张图、{len(emotions)} 种情绪辽 🎀✨"]
    for emo in emotions:
        imgs = len(emoji_system.EMOJI_FILES[emo])
        kws = len(emoji_system.EMOJI_EMOTIONS.get(emo, []))
        lines.append(f"  {emo}: {imgs} 图片, {kws} 关键词")
    return True, "\n".join(lines)


def cmd_testmode(event: Event, arg: str) -> tuple[bool, str]:
    """测试模式开关（仅管理员）"""
    if (r := _require_admin(event)):
        return r
    sub = arg.strip().lower()
    current = cfg.TEST_MODE_ENABLED
    if sub in ("on", "开", "开启", "1"):
        new_val = True
    elif sub in ("off", "关", "关闭", "0"):
        new_val = False
    else:
        status = "开启 🔒" if current else "关闭 🔓"
        return True, f"当前测试模式：{status}\n用 /testmode on 开启，/testmode off 关闭"
    settings_file = cfg._RUNTIME_CONFIG_FILE
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
        data["test_mode_enabled"] = new_val
        settings_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        cfg.SETTINGS_RELOAD_TRIGGER.write_text("testmode via chat")
    except Exception as e:
        return True, f"切换失败：{e}"
    status = "开启" if new_val else "关闭"
    emoji = "🔒" if new_val else "🔓"
    return True, (
        f"测试模式已{status} {emoji}\n"
        f"{'现在只有泥能跟窝聊天啦～' if new_val else '所有人都可以来找窝玩啦～'}"
    )


# 命令注册表：name → (aliases, handler, needs_bot, needs_arg)
# 所有命令仅管理员可用（admin 校验在每个 cmd_* 内部统一处理）
COMMAND_REGISTRY = {
    "reset": ({"重置", "清空记忆"}, cmd_reset, False, False),
    "skills": ({"角色", "列表"}, cmd_skills, False, False),
    "switch": ({"切换"}, cmd_switch, True, True),
    "current": ({"当前"}, cmd_current, False, False),
    "reloademoji": ({"重载表情"}, cmd_reloademoji, False, False),
    "testmode": ({"测试模式"}, cmd_testmode, False, True),
}


def dispatch_command(cmd: str, arg: str, bot: Bot, event: Event) -> tuple[bool, str] | None:
    """统一命令分发入口。返回 None 表示命令不识别。"""
    for name, (aliases, handler, needs_bot, needs_arg) in COMMAND_REGISTRY.items():
        if cmd == name or cmd in aliases:
            if needs_bot:
                return handler(bot, event, arg)
            return handler(event, arg) if needs_arg else handler(event)
    return None


def _safe_create_task(coro, name: str = ""):
    """创建带错误日志的后台任务"""
    from nonebot import logger
    task = asyncio.create_task(coro, name=name)

    def _on_done(t):
        if not t.cancelled() and t.exception():
            logger.error(f"Background task '{name}' failed: {t.exception()}")

    task.add_done_callback(_on_done)
    return task
