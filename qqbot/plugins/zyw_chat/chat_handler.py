"""
主聊天处理器（handle_chat）
"""

import asyncio
import re
import time
from datetime import datetime

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, PrivateMessageEvent
from nonebot.rule import Rule

from . import config as cfg
from . import history as hist
from . import skill_manager
from . import avatar
from . import emoji_system
from . import api_client
from . import provider as prov_mod
from . import llm
from . import url_fetcher
from .rich_message import parse_rich_message
from .rules import respond_rule
from .dsml_cleaner import strip_dsml_markup
from .message_utils import normalize_qq_faces, parse_qq_faces, split_message


# --- 消息处理器 ---

zyw_chat = on_message(rule=Rule(respond_rule), priority=50, block=True)


def _safe_create_task(coro, name: str = ""):
    """创建带错误日志的后台任务"""
    task = asyncio.create_task(coro, name=name)
    def _on_done(t):
        if not t.cancelled() and t.exception():
            logger.error(f"Background task '{name}' failed: {t.exception()}")
    task.add_done_callback(_on_done)
    return task


@zyw_chat.handle()
async def handle_chat(bot: Bot, event: Event):
    logger.info(f"[DEBUG] Message received: type={type(event).__name__}, user={event.get_user_id()}")

    # 活跃时间检查
    now = datetime.now()
    if now.hour < cfg.ACTIVE_HOURS_START or now.hour > cfg.ACTIVE_HOURS_END:
        return

    text = event.get_plaintext().strip()
    if not text:
        text = parse_rich_message(event)
    if not text:
        return

    # 过滤掉 @ bot 的部分（群聊）
    if isinstance(event, GroupMessageEvent):
        clean_parts = []
        for seg in event.message:
            if seg.type == "text":
                clean_parts.append(seg.data.get("text", ""))
            elif seg.type == "at":
                continue
            elif seg.type in ("json", "xml", "share"):
                rich = parse_rich_message(event)
                if rich and rich not in "".join(clean_parts):
                    clean_parts.append(rich)
            else:
                clean_parts.append(str(seg))
        text = "".join(clean_parts).strip()
        if not text:
            return

    # 手动命令分发（群聊 @Bot 后 text 开头有空格，on_command 无法匹配）
    if text.startswith("/") or text.startswith("／"):
        parts = text[1:].strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("reset", "重置", "清空记忆"):
            key = hist.get_history_key(event)
            hist.clear_history(key)
            await zyw_chat.finish("好叭！窝把之前聊的都忘光光啦 (◍•ᴗ•◍)❤ 重新开始叭～")
            return

        if cmd in ("skills", "角色", "列表"):
            active = hist.global_active_skill or skill_manager.DEFAULT_SKILL_NAME or ""
            if not skill_manager.ALL_SKILLS:
                await zyw_chat.finish("呜哇！现在窝这里一个角色都没有加载捏 [委屈]")
                return
            lines = ["泥可以选这些角色陪泥聊天哦：\n"]
            for name, skill in skill_manager.ALL_SKILLS.items():
                marker = " 👈 当前" if name == active else ""
                desc = skill.description or "暂无描述"
                ver = f" v{skill.version}" if skill.version else ""
                lines.append(f"  {skill.display_name}({name}){ver} — {desc}{marker}")
            lines.append(f"\n发送 /switch <名字> 就能换人啦～")
            await zyw_chat.finish("\n".join(lines))
            return

        if cmd in ("switch", "切换"):
            uid = event.get_user_id()
            if uid != str(cfg.ADMIN_QQ):
                await zyw_chat.finish("欸——这个只有窝才可以操作哦 🙈 对不起嘛！")
                return
            if not arg:
                names = ", ".join(f"{s.display_name}({n})" for n, s in skill_manager.ALL_SKILLS.items())
                await zyw_chat.finish(f"要用这个格式哦：/switch <角色名> ✨\n可选的有：{names} 💕")
                return
            if arg not in skill_manager.ALL_SKILLS:
                names = ", ".join(f"{s.display_name}({n})" for n, s in skill_manager.ALL_SKILLS.items())
                await zyw_chat.finish(f"呜呜 '{arg}' 这个角色窝找不到捏 [抓狂]\n这些是窝有的：{names} 泥再挑挑？")
                return

            hist.global_active_skill = arg
            _safe_create_task(avatar.set_profile(bot, arg), f"profile-{arg}")

            skill = skill_manager.ALL_SKILLS[arg]
            await zyw_chat.finish(
                f"切换成功惹！现在是 {skill.display_name}({arg}) 在陪泥啦 🧡🌟\n"
                f"{skill.description or ''}\n"
                f"之后大家聊天都会用这个角色辽～"
            )
            return

        if cmd in ("current", "当前"):
            from . import provider as prov
            active = hist.global_active_skill or skill_manager.DEFAULT_SKILL_NAME or ""
            skill = skill_manager.ALL_SKILLS.get(active)
            if skill:
                p = prov.get_active_provider()
                await zyw_chat.finish(
                    f"听好惹！现在是 {skill.display_name}({skill.name}) 在陪泥聊天哟💕\n"
                    f"{skill.description or '暂无描述'}\n"
                    f"版本：{skill.version or '?'} 📱✨\n"
                    f"模型：{p['model']} ({p['name']}) 🧠💭"
                )
            else:
                await zyw_chat.finish("窝还没有给泥准备可以聊天的角色呢 [对手指] 泥想选谁呀？")
            return

        if cmd in ("reloademoji", "重载表情"):
            if event.get_user_id() != str(cfg.ADMIN_QQ):
                await zyw_chat.finish("这个也是只有窝才能弄的啦 🙈💦")
                return
            emoji_system.load_emoji_files()
            total_imgs = sum(len(v) for v in emoji_system.EMOJI_FILES.values())
            emotions = list(emoji_system.EMOJI_FILES.keys())
            lines = [f"叮咚～窝重新整理了一下表情包包！现在有 {total_imgs} 张图、{len(emotions)} 种情绪辽 🎀✨"]
            for emo in emotions:
                imgs = len(emoji_system.EMOJI_FILES[emo])
                kws = len(emoji_system.EMOJI_EMOTIONS.get(emo, []))
                lines.append(f"  {emo}: {imgs} 图片, {kws} 关键词")
            await zyw_chat.finish("\n".join(lines))
            return

    uid = event.get_user_id()
    history_key = hist.get_history_key(event)

    # ── URL 提取与异步抓取 ──
    _msg_urls = url_fetcher.extract_urls(text)
    _url_fetch_task = None
    if _msg_urls:
        cfg.chat_logger.info(f"[URL] 检测到 URL: {_msg_urls}")
        _url_fetch_task = asyncio.create_task(url_fetcher.process_message_urls(_msg_urls))

    # ── 用户级排队 ──
    user_lock = api_client.get_user_lock(uid)
    if user_lock.locked():
        cfg.chat_logger.info(f"[QUEUE] user={uid} 正在排队等待上一条消息处理完成")
    async with user_lock:

        # ── 提取说话人身份 ──
        sender_name = ""
        if isinstance(event, GroupMessageEvent):
            sender_name = getattr(event.sender, "nickname", "") or getattr(event.sender, "card", "") or ""
        elif isinstance(event, PrivateMessageEvent):
            sender_name = getattr(event.sender, "nickname", "") or ""
        sender_name = sender_name.strip()

        # 获取当前全局角色的 system prompt
        active_name = hist.global_active_skill or skill_manager.DEFAULT_SKILL_NAME or ""
        skill = skill_manager.ALL_SKILLS.get(active_name)
        if not skill:
            await zyw_chat.finish("窝懵惹！没有角色在和泥聊天呢 [委屈] 先用 /skills 看看有谁可以陪泥叭～")
            return

        # 注入说话人身份到 system prompt
        effective_prompt = skill.system_prompt
        context_parts = []
        if sender_name:
            context_parts.append(f"当前和你聊天的人叫「{sender_name}」，你可以在回复中自然地称呼对方，但不要刻意重复名字")

        # ── 用户画像注入 ──
        from . import user_profile
        profile_text = user_profile.get_profile_summary(uid, active_name)
        if profile_text:
            context_parts.append(f"用户画像（{profile_text}）")

        # ── 上下文风格自适应 ──
        style_hints = []

        if len(text) <= 8:
            style_hints.append("对方消息很短，回复应简洁自然，一两句话即可")
        elif len(text) > 200:
            style_hints.append("对方说了很多，可以适当详细回应，但不要长篇大论")

        if re.search(r'[？?]', text):
            style_hints.append("对方在提问或寻求回应，请给出有内容的回答")

        if isinstance(event, GroupMessageEvent):
            style_hints.append("这是群聊场景，回复应轻松简短，像朋友闲聊")
        else:
            style_hints.append("这是私聊，可以更亲密、自然地交流")

        recent = hist.conversation_histories[history_key][-4:]
        user_msgs = [m["content"] for m in recent if m["role"] == "user"]
        if user_msgs and all(len(m) <= 15 for m in user_msgs):
            style_hints.append("对话节奏很快（对方一直发短消息），保持简短回复")

        if _msg_urls:
            style_hints.append("对方分享了一个链接，请根据链接内容给出有内容的回应，不要因为消息短就回复简短")

        if style_hints:
            context_parts.append("回复风格参考：" + "；".join(style_hints))

        # ── 注入 URL 内容到上下文 ──
        if _url_fetch_task is not None:
            try:
                _url_context = await _url_fetch_task
                if _url_context:
                    context_parts.append(_url_context)
                    cfg.chat_logger.info(f"[URL] 已注入 URL 内容到上下文 ({len(_url_context)} 字符)")
            except Exception as e:
                logger.warning(f"[URL] 获取 URL 内容失败: {e}")

        if context_parts:
            effective_prompt = effective_prompt + "\n\n[" + "]\n[".join(context_parts) + "]"

        # 添加到历史
        hist.add_to_history(history_key, "user", text)

        # 记录收到的消息
        chat_type = "GROUP" if isinstance(event, GroupMessageEvent) else "PRIVATE"
        gid = getattr(event, "group_id", "-")
        cfg.chat_logger.info(
            f"[MSG] {chat_type} | group={gid} | user={uid} | sender={sender_name or 'N/A'} "
            f"| skill={active_name} | text='{text[:80]}{'...' if len(text)>80 else ''}'"
        )

        # ── 等待提示 + 流式输出 ──
        _thinking_sent = False
        _timer_cancelled = False

        async def _thinking_timer():
            nonlocal _thinking_sent
            await asyncio.sleep(cfg.THINKING_TIMER_SECONDS)
            if not _timer_cancelled:
                try:
                    await zyw_chat.send(Message("唔…让窝想想哦 (´•ω•̥`)💭"))
                    _thinking_sent = True
                except Exception:
                    pass

        timer_task = asyncio.create_task(_thinking_timer())

        # ── 流式决策 ──
        _probe_skipped = False
        if active_name and cfg.STREAM_ENABLED:
            if cfg.has_search_intent(text):
                use_stream = False
                cfg.chat_logger.info(f"[PROBE] 检测到搜索意图关键词，跳过探测，直接走工具调用")
            else:
                needs_tools = await llm.probe_tool_usage(
                    effective_prompt, hist.conversation_histories[history_key], active_name
                )
                if not needs_tools:
                    use_stream = True
                    _probe_skipped = True
                    cfg.chat_logger.info("[PROBE] 无需工具，切换流式输出")
                else:
                    use_stream = False
        else:
            use_stream = cfg.STREAM_ENABLED and not (cfg.WEB_SEARCH_ENABLED and cfg.SEARCH_AVAILABLE) and not active_name

        if use_stream:
            # ── 流式输出 ──
            _sentence_end = set("。！？\n!?")
            full_reply = ""
            current_segment = ""
            sent_count = 0
            last_flush_time = time.time()
            _stream_provider = prov_mod.get_active_provider()

            try:
                async for token in api_client.api_request_stream({
                    "model": _stream_provider["model"],
                    "reasoning_effort": "high",
                    "temperature": 0.95,
                    "top_p": 0.9,
                    "max_tokens": 2048,
                    "frequency_penalty": 0.3,
                    "presence_penalty": 0.2,
                    "messages": [{"role": "system", "content": effective_prompt}]
                                + hist.conversation_histories[history_key],
                }):
                    if not _timer_cancelled:
                        _timer_cancelled = True
                        timer_task.cancel()

                    current_segment += token
                    full_reply += token
                    now = time.time()

                    should_flush = (
                        (len(current_segment) >= cfg.STREAM_FLUSH_CHARS
                         and current_segment[-1] in _sentence_end)
                        or len(current_segment) >= cfg.STREAM_MAX_FLUSH_SIZE
                    )

                    if should_flush:
                        _flush_reason = "max_size" if len(current_segment) >= cfg.STREAM_MAX_FLUSH_SIZE else "sentence_end"
                        seg_text = strip_dsml_markup(normalize_qq_faces(current_segment.strip()))
                        current_segment = ""
                        last_flush_time = time.time()
                        if seg_text:
                            cfg.chat_logger.info(
                                f"[STREAM_SEG] reason={_flush_reason} | "
                                f"len={len(seg_text)} | text='{seg_text[:80]}{'...' if len(seg_text)>80 else ''}'"
                            )
                            try:
                                await zyw_chat.send(parse_qq_faces(seg_text))
                                sent_count += 1
                            except Exception as e:
                                logger.warning(f"[STREAM] send error: {e}")
                                break
            except Exception as e:
                logger.warning(f"[STREAM] streaming error: {e}")
            finally:
                timer_task.cancel()

            # 处理尾部残留内容
            if current_segment.strip():
                seg_text = strip_dsml_markup(normalize_qq_faces(current_segment.strip()))
                if seg_text:
                    try:
                        await zyw_chat.send(parse_qq_faces(seg_text))
                        sent_count += 1
                    except Exception:
                        pass

            reply = strip_dsml_markup(full_reply.strip()) if full_reply.strip() else None
            _stream_already_sent = sent_count > 0

            # 流式输出清洗后为空，回退到非流式
            if not reply and not _stream_already_sent:
                cfg.chat_logger.warning("[STREAM] 流式输出清洗后为空（可能是工具调用泄露），回退到非流式")
                try:
                    reply = await llm.call_deepseek(effective_prompt, hist.conversation_histories[history_key], skill_name=active_name)
                except Exception as e:
                    logger.warning(f"[STREAM] fallback error: {e}")
        else:
            # ── 非流式调用 ──
            try:
                reply = await llm.call_deepseek(effective_prompt, hist.conversation_histories[history_key], skill_name=active_name)
            finally:
                _timer_cancelled = True
                timer_task.cancel()
            _stream_already_sent = False

        if reply is None:
            reply = "啊啊窝的脑袋当机惹 [抓狂] 让窝休息一下下再来陪泥聊天好不好 ꒰ᐢ⸝⸝⸝⸝⸝⸝ᐢ꒱"

        # 规范化 QQ 表情格式
        if not use_stream:
            reply = normalize_qq_faces(reply)

        # 添加到历史
        hist.add_to_history(history_key, "assistant", reply)
        hist.save_histories()

        # ── 用户画像更新触发 ──
        user_profile.profile_turn_count[uid] = user_profile.profile_turn_count.get(uid, 0) + 1
        last_update = user_profile.profile_last_update.get(uid, 0)
        if (user_profile.profile_turn_count[uid] >= user_profile._PROFILE_UPDATE_MIN_TURNS
                and time.time() - last_update > user_profile._PROFILE_UPDATE_INTERVAL):
            user_profile.profile_turn_count[uid] = 0
            user_profile.profile_last_update[uid] = time.time()
            cfg.chat_logger.info(f"[PROFILE] 触发画像提取 uid={uid}, skill={active_name}")
            _safe_create_task(
                user_profile.extract_user_profile(uid, history_key, active_name),
                f"profile-{uid}",
            )

        # ── 非流式分段发送 ──
        if not _stream_already_sent:
            segments = split_message(reply)
            cfg.chat_logger.info(
                f"[REPLY] user={uid} | segments={len(segments)} "
                f"| reply='{reply[:100]}{'...' if len(reply)>100 else ''}'"
            )

            if len(segments) == 1:
                await zyw_chat.send(parse_qq_faces(segments[0]))
            else:
                sent_count = 0
                try:
                    for i, seg in enumerate(segments):
                        await zyw_chat.send(parse_qq_faces(seg))
                        sent_count += 1
                        if i < len(segments) - 1:
                            delay = min(0.5 + len(seg) / 300, 2.5)
                            await asyncio.sleep(delay)
                except Exception as e:
                    logger.warning(f"[REPLY] 分段发送中断 ({sent_count}/{len(segments)}): {e}")
                    remaining = "\n\n".join(segments[sent_count:])
                    if remaining.strip():
                        try:
                            await zyw_chat.send(parse_qq_faces(remaining))
                        except Exception:
                            pass
        else:
            cfg.chat_logger.info(
                f"[STREAM] user={uid} | segments={sent_count} "
                f"| reply='{reply[:100]}{'...' if len(reply)>100 else ''}'"
            )

        # ── 情绪表情 ──
        if reply and reply != "啊啊窝的脑袋当机惹 [抓狂] 让窝休息一下下再来陪泥聊天好不好 ꒰ᐢ⸝⸝⸝⸝⸝⸝ᐢ꒱":
            try:
                await emoji_system.maybe_send_emoji(bot, event, text, reply, uid)
            except Exception as e:
                logger.debug(f"[EMOJI] error: {e}")
