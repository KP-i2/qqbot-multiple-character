"""
命令注册与处理器（reset / skills / switch / current / reloademoji）
"""

import nonebot
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.rule import to_me

from . import config as cfg
from . import history as hist
from . import skill_manager
from . import avatar
from . import emoji_system
from . import api_client


# --- 命令注册 ---

cmd_reset = on_command("reset", aliases={"重置", "清空记忆"}, rule=to_me(), priority=1, block=True)
cmd_skills = on_command("skills", aliases={"角色", "列表"}, rule=to_me(), priority=1, block=True)
cmd_switch = on_command("switch", aliases={"切换"}, rule=to_me(), priority=1, block=True)
cmd_current = on_command("current", aliases={"当前"}, rule=to_me(), priority=1, block=True)
cmd_reloademoji = on_command("reloademoji", aliases={"重载表情"}, rule=to_me(), priority=1, block=True)


@cmd_reset.handle()
async def handle_reset(event: Event):
    uid = event.get_user_id()
    user_lock = api_client.get_user_lock(uid)
    async with user_lock:
        key = hist.get_history_key(event)
        hist.clear_history(key)
        await cmd_reset.finish("好叭！窝把之前聊的都忘光光啦 (◍•ᴗ•◍)❤ 重新开始叭～")
    api_client.release_user_lock(uid)


@cmd_skills.handle()
async def handle_skills(event: Event):
    active = hist.global_active_skill or skill_manager.DEFAULT_SKILL_NAME or ""

    if not skill_manager.ALL_SKILLS:
        await cmd_skills.finish("呜哇！现在窝这里一个角色都没有加载捏 [委屈]")
        return

    lines = ["泥可以选这些角色陪泥聊天哦：\n"]
    for name, skill in skill_manager.ALL_SKILLS.items():
        marker = " 👈 当前" if name == active else ""
        desc = skill.description or "暂无描述"
        ver = f" v{skill.version}" if skill.version else ""
        lines.append(f"  {skill.display_name}({name}){ver} — {desc}{marker}")

    lines.append(f"\n发送 /switch <名字> 就能换人啦～")
    await cmd_skills.finish("\n".join(lines))


@cmd_switch.handle()
async def handle_switch(bot: Bot, event: Event):
    uid = event.get_user_id()

    if uid != str(cfg.ADMIN_QQ):
        await cmd_switch.finish("欸——这个只有窝才可以操作哦 🙈 对不起嘛！")
        return

    user_lock = api_client.get_user_lock(uid)
    async with user_lock:
        text = event.get_plaintext().strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            names = ", ".join(f"{s.display_name}({n})" for n, s in skill_manager.ALL_SKILLS.items())
            await cmd_switch.finish(f"要用这个格式哦：/switch <角色名> ✨\n可选的有：{names} 💕")
            return

        target = parts[1].strip()

        if target not in skill_manager.ALL_SKILLS:
            names = ", ".join(f"{s.display_name}({n})" for n, s in skill_manager.ALL_SKILLS.items())
            await cmd_switch.finish(f"呜呜 '{target}' 这个角色窝找不到捏 [抓狂]\n这些是窝有的：{names} 泥再挑挑？")
            return

        hist.global_active_skill = target

        _safe_create_task(avatar.set_profile(bot, target), f"profile-{target}")

        skill = skill_manager.ALL_SKILLS[target]
        await cmd_switch.finish(
            f"切换成功惹！现在是 {skill.display_name}({target}) 在陪泥啦 🧡🌟\n"
            f"{skill.description or ''}\n"
            f"之后大家聊天都会用这个角色辽～"
        )
    api_client.release_user_lock(uid)


@cmd_current.handle()
async def handle_current(event: Event):
    from . import provider as prov
    active = hist.global_active_skill or skill_manager.DEFAULT_SKILL_NAME or ""
    skill = skill_manager.ALL_SKILLS.get(active)
    if skill:
        p = prov.get_active_provider()
        await cmd_current.finish(
            f"听好惹！现在是 {skill.display_name}({skill.name}) 在陪泥聊天哟💕\n"
            f"{skill.description or '暂无描述'}\n"
            f"版本：{skill.version or '?'} 📱✨\n"
            f"模型：{p['model']} ({p['name']}) 🧠💭"
        )
    else:
        await cmd_current.finish("窝还没有给泥准备可以聊天的角色呢 [对手指] 泥想选谁呀？")


@cmd_reloademoji.handle()
async def handle_reloademoji(event: Event):
    uid = event.get_user_id()
    if uid != str(cfg.ADMIN_QQ):
        await cmd_reloademoji.finish("这个也是只有窝才能弄的啦 🙈💦")
        return
    emoji_system.load_emoji_files()
    total_imgs = sum(len(v) for v in emoji_system.EMOJI_FILES.values())
    emotions = list(emoji_system.EMOJI_FILES.keys())
    lines = [f"叮咚～窝重新整理了一下表情包包！现在有 {total_imgs} 张图、{len(emotions)} 种情绪辽 🎀✨"]
    for emo in emotions:
        imgs = len(emoji_system.EMOJI_FILES[emo])
        kws = len(emoji_system.EMOJI_EMOTIONS.get(emo, []))
        lines.append(f"  {emo}: {imgs} 图片, {kws} 关键词")
    await cmd_reloademoji.finish("\n".join(lines))


def _safe_create_task(coro, name: str = ""):
    """创建带错误日志的后台任务"""
    import asyncio
    task = asyncio.create_task(coro, name=name)
    def _on_done(t):
        if not t.cancelled() and t.exception():
            from nonebot import logger
            logger.error(f"Background task '{name}' failed: {t.exception()}")
    task.add_done_callback(_on_done)
    return task
