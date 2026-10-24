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
from . import turn_recorder
from . import url_fetcher
from . import image_handler
from .rich_message import parse_rich_message
from .rules import respond_rule
from .dsml_cleaner import strip_dsml_markup
from .message_utils import normalize_qq_faces, parse_qq_faces, split_message, split_at_breath_pauses, dedup_consecutive_chunks, clean_chat_output


# --- 消息处理器 ---

char_chat = on_message(rule=Rule(respond_rule), priority=50, block=True)


def _safe_create_task(coro, name: str = ""):
    """创建带错误日志的后台任务"""
    task = asyncio.create_task(coro, name=name)
    def _on_done(t):
        if not t.cancelled() and t.exception():
            logger.error(f"Background task '{name}' failed: {t.exception()}")
    task.add_done_callback(_on_done)
    return task


@char_chat.handle()
async def handle_chat(bot: Bot, event: Event):
    """入口：包一层 turn 记录（dashboard「实时活动」面板 + 落盘 turns_stream.jsonl）

    2026-09-10 新增：此前 begin_turn/finish_turn 全项目没有任何地方调用，
    所有 step 都成孤儿（只写 _overflow/），dashboard 面板对聊天链路永远是空的。
    """
    _plain = event.get_plaintext().strip()
    turn_id = turn_recorder.begin_turn({
        "user": str(event.get_user_id()),
        "group": str(getattr(event, "group_id", "") or ""),
        "text": _plain[:200],
    })
    turn_recorder.add_step({
        "kind": "msg",
        "title": "收到消息",
        "data": {"user": str(event.get_user_id()), "text": _plain[:400]},
    })
    status = "ok"
    try:
        await _handle_chat_inner(bot, event)
    except Exception:
        status = "error"
        raise
    finally:
        turn_recorder.finish_turn(status, turn_id)


async def _handle_chat_inner(bot: Bot, event: Event):
    logger.info(f"[DEBUG] Message received: type={type(event).__name__}, user={event.get_user_id()}")

    # 活跃时间检查
    now = datetime.now()
    if now.hour < cfg.ACTIVE_HOURS_START or now.hour > cfg.ACTIVE_HOURS_END:
        return

    text = event.get_plaintext().strip()
    if not text:
        text = parse_rich_message(event)

    # 2026-07-08: 抽取 image 段, 调 vision provider 把图转成文字描述
    # 解决: 之前 bot 收到纯图片消息时, parse_rich_message 对 image 类型显式跳过,
    #       get_plaintext 也返回空, 导致 if not text: return 直接退出, 用户没有反馈
    # 2026-07-23: 加 cfg.IMAGE_HANDLER_ENABLED 开关 — minimax API 到期时 dashboard 关掉即停用 vision
    _images = image_handler.extract_image_segments(event) if cfg.IMAGE_HANDLER_ENABLED else []
    if _images:
        cfg.chat_logger.info(f"[IMAGE] 检测到 {len(_images)} 张图片, 准备用 vision provider 描述")
        _img_descs = []
        for _img_data in _images:
            _url = image_handler._resolve_image_url(_img_data)
            if not _url:
                cfg.chat_logger.warning(f"[IMAGE] 图片 URL 解析失败: {_img_data}")
                continue
            _desc = await prov_mod.describe_image(_url, text or "")
            if _desc:
                _img_descs.append(_desc)
                cfg.chat_logger.info(f"[IMAGE] vision 描述完成, 长度 {len(_desc)} 字")
        if _img_descs:
            # 2026-08-30: 包装方式收敛到 prov.wrap_image_description ——
            # 此前这里用 `[图片描述: ...]` 标签，而 provider.describe_images_in_payload
            # 用的是 `【图片内容】`+反复述指令。2026-07-23 就判定标签式写法不好，
            # 但那次只改到了实际不走的防御分支，真正生效的主路径（这里）一直没跟上。
            # 顺带：多图从 "; " 拼接改为空行分隔，读起来更像"看到了几张图"。
            _img_summary = prov_mod.wrap_image_description("\n\n".join(_img_descs))
            text = f"{text}\n\n{_img_summary}" if text else _img_summary
            cfg.chat_logger.info(f"[IMAGE] 合并到 prompt, 描述总长 {sum(len(d) for d in _img_descs)} 字")
        else:
            # 解析全失败, 给出降级占位符
            if not text:
                text = "[用户发送了图片, 但 bot 暂时无法识别]"
            cfg.chat_logger.warning(f"[IMAGE] 所有图片描述都失败, 降级为占位符")

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
            await char_chat.finish("✅ 对话历史已清空。")
            return

        if cmd in ("skills", "角色", "列表"):
            active = hist.global_active_skill or skill_manager.DEFAULT_SKILL_NAME or ""
            if not skill_manager.ALL_SKILLS:
                await char_chat.finish("呜哇！现在窝这里一个角色都没有加载捏 [委屈]")
                return
            lines = ["📋 可用角色：\n"]
            for name, skill in skill_manager.ALL_SKILLS.items():
                marker = "  👈 当前" if name == active else ""
                desc = skill.description or "暂无描述"
                ver = f" v{skill.version}" if skill.version else ""
                lines.append(f"  {skill.display_name} ({name}){ver} — {desc}{marker}")
            lines.append("\n用 /switch <名字> 切换")
            await char_chat.finish("\n".join(lines))
            return

        if cmd in ("switch", "切换"):
            uid = event.get_user_id()
            if uid != str(cfg.ADMIN_QQ):
                await char_chat.finish("欸——这个只有窝才可以操作哦 🙈 对不起嘛！")
                return
            if not arg:
                names = ", ".join(f"{s.display_name}({n})" for n, s in skill_manager.ALL_SKILLS.items())
                await char_chat.finish(f"要用这个格式哦：/switch <角色名> ✨\n可选的有：{names} 💕")
                return
            if arg not in skill_manager.ALL_SKILLS:
                names = ", ".join(f"{s.display_name}({n})" for n, s in skill_manager.ALL_SKILLS.items())
                await char_chat.finish(f"呜呜 '{arg}' 这个角色窝找不到捏 [抓狂]\n这些是窝有的：{names} 泥再挑挑？")
                return

            hist.global_active_skill = arg
            _safe_create_task(avatar.set_profile(bot, arg), f"profile-{arg}")

            skill = skill_manager.ALL_SKILLS[arg]
            await char_chat.finish(
                f"✅ 已切换到 {skill.display_name} ({arg})\n"
                f"{skill.description or ''}"
            )
            return

        if cmd in ("current", "当前"):
            from . import provider as prov
            active = hist.global_active_skill or skill_manager.DEFAULT_SKILL_NAME or ""
            skill = skill_manager.ALL_SKILLS.get(active)
            if skill:
                p = prov.get_active_provider()
                await char_chat.finish(
                    f"📌 当前角色：{skill.display_name} ({skill.name}) v{skill.version or '?'}\n"
                    f"模型：{p['model']} ({p['name']})"
                )
            else:
                await char_chat.finish("窝还没有给泥准备可以聊天的角色呢 [对手指] 泥想选谁呀？")
            return

        if cmd in ("reloademoji", "重载表情"):
            if event.get_user_id() != str(cfg.ADMIN_QQ):
                await char_chat.finish("这个也是只有窝才能弄的啦 🙈💦")
                return
            emoji_system.load_emoji_files()
            total_imgs = sum(len(v) for v in emoji_system.EMOJI_FILES.values())
            emotions = list(emoji_system.EMOJI_FILES.keys())
            lines = [f"✅ 表情已重载：{total_imgs} 张图、{len(emotions)} 种情绪"]
            for emo in emotions:
                imgs = len(emoji_system.EMOJI_FILES[emo])
                kws = len(emoji_system.EMOJI_EMOTIONS.get(emo, []))
                lines.append(f"  {emo}: {imgs} 图片, {kws} 关键词")
            await char_chat.finish("\n".join(lines))
            return

        if cmd in ("testmode", "测试模式"):
            if event.get_user_id() != str(cfg.ADMIN_QQ):
                await char_chat.finish("这个只有管理员才能操作哦 🙈")
                return
            sub = arg.strip().lower()
            current = cfg.TEST_MODE_ENABLED
            if sub in ("on", "开", "开启", "1"):
                new_val = True
            elif sub in ("off", "关", "关闭", "0"):
                new_val = False
            else:
                # 不带参数：仅显示当前状态
                status = "开启 🔒" if current else "关闭 🔓"
                await char_chat.finish(f"当前测试模式：{status}\n用 /testmode on 开启，/testmode off 关闭")
                return
            import json as _json
            settings_file = cfg._RUNTIME_CONFIG_FILE
            try:
                data = _json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
                data["test_mode_enabled"] = new_val
                settings_file.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                cfg.SETTINGS_RELOAD_TRIGGER.write_text(f"testmode via chat")
            except Exception as e:
                await char_chat.finish(f"切换失败：{e}")
                return
            status = "开启" if new_val else "关闭"
            emoji = "🔒" if new_val else "🔓"
            await char_chat.finish(f"✅ 测试模式已{status} {emoji}")
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
            await char_chat.finish("⚠️ 当前没有加载角色，请用 /skills 查看可用角色。")
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

        # ── 主动暗示掷骰（角色级，默认关闭；见 initiate.py）──
        # 概率由代码掷，命中才注入 skill.initiate_hint；没命中什么都不加。
        try:
            from . import initiate as initiate_mod
            _initiate_hint = initiate_mod.hint_for(
                history_key, skill,
                is_group=isinstance(event, GroupMessageEvent),
                uid=uid,
            )
            if _initiate_hint:
                context_parts.append(_initiate_hint)
                cfg.chat_logger.info("[initiate] 已注入主动暗示提示")
        except Exception as e:
            logger.warning(f"[initiate] 掷骰失败: {e}")

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

        # 多轮对话开关：关闭时只保留当前消息，不传历史上下文
        _effective_history = hist.conversation_histories[history_key] if cfg.MULTI_TURN_ENABLED else []

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
                    await char_chat.send(Message("唔…让窝想想哦 (´•ω•̥`)💭"))
                    _thinking_sent = True
                except Exception:
                    pass

        timer_task = asyncio.create_task(_thinking_timer())

        # ── 流式决策 ──
        # 2026-09-12: 搜索触发权交给模型 —— 不再用关键词短路 probe。
        # 关键词命中只作为日志信号，并在下面往 system 里加一句提示；
        # "要不要用工具"完全由模型自己（probe 的 tool_choice=auto）决定。
        _kw_hint = cfg.has_search_intent(text)
        # 2026-09-13 修复：关键词提示必须拼在 probe **之前**。
        # 之前这句提示只在非流式分支里加，而路由是 probe 做的 —— 等于路由器看不到任何提示，
        # 于是"你们这周在哪演出"被判成"无需工具"，模型只能凭记忆编一个场馆和时间。
        _probe_prompt = effective_prompt
        if _kw_hint:
            _probe_prompt = effective_prompt + (
                "\n\n[提示] 这句话可能在问你的知识范围之外的具体信息（现实世界的安排 / 最新动态 / 外部事实）。"
                "你的记忆里没有这些数据：不确定就先调用 web_search（角色相关可以先用 search_corpus）查证再回答，"
                "绝对不要凭记忆编一个具体的场馆、时间或人名；查不到就说不确定。"
                "如果确定、或者只是日常闲聊，就不用搜。"
            )
            cfg.chat_logger.info("[SEARCH] 关键词命中 → 提示已拼在 probe 之前")
        if active_name and cfg.STREAM_ENABLED:
            needs_tools = await llm.probe_tool_usage(
                _probe_prompt, _effective_history, active_name
            )
            if not needs_tools:
                use_stream = True
                cfg.chat_logger.info(
                    "[PROBE] 模型判定无需工具 → 流式输出"
                    + ("（关键词命中，仅作提示）" if _kw_hint else "")
                )
            else:
                use_stream = False
                cfg.chat_logger.info(
                    "[PROBE] 模型判定需要工具 → 工具循环"
                    + ("（关键词命中，仅作提示）" if _kw_hint else "")
                )
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
            turn_recorder.add_step({
                "kind": "llm_call",
                "title": f"流式调用 {_stream_provider['name']} ({_stream_provider['model']})",
                "data": {
                    "provider": _stream_provider["name"],
                    "model": _stream_provider["model"],
                    "messages_count": len(_effective_history) + 1,
                    "prompt_chars": len(effective_prompt),
                    "skill": active_name,
                },
            })

            try:
                # 2026-08-30 修复: 采样参数改走 llm.build_sampling_params，不再硬编码。
                # 此前这里写死 temperature=0.95 / top_p=0.9 / max_tokens=8192 / reasoning_effort="high"，
                # 导致 Dashboard「参数设置」里的 llm_temperature / llm_top_p / llm_max_tokens
                # 对走流式的消息完全无效（只有非流式跟随），且 max_tokens 被砍半到 8192，
                # 长回复容易被截断（代码里原有的"疑似截断→非流式补救"补丁就是在兜这个）。
                async for token in api_client.api_request_stream({
                    "model": _stream_provider["model"],
                    **llm.build_sampling_params(_stream_provider["name"]),
                    "messages": [{"role": "system", "content": effective_prompt}]
                                + _effective_history,
                }):
                    if not _timer_cancelled:
                        _timer_cancelled = True
                        timer_task.cancel()

                    current_segment += token
                    full_reply += token
                    now = time.time()

                    # 断句触发条件（三选一）:
                    # 1) 字数达标 + 句末标点
                    # 2) 硬上限（强制截断）
                    # 3) 时间间隔达标 + 最小字数
                    _time_elapsed = now - last_flush_time
                    _time_trigger = (
                        _time_elapsed >= cfg.STREAM_FLUSH_INTERVAL
                        and len(current_segment) >= cfg.STREAM_FLUSH_MIN_CHARS
                    )
                    should_flush = (
                        (len(current_segment) >= cfg.STREAM_FLUSH_CHARS
                         and current_segment[-1] in _sentence_end)
                        or len(current_segment) >= cfg.STREAM_MAX_FLUSH_SIZE
                        or _time_trigger
                    )

                    if should_flush:
                        if len(current_segment) >= cfg.STREAM_MAX_FLUSH_SIZE:
                            _flush_reason = "max_size"
                        elif _time_trigger:
                            _flush_reason = f"time({_time_elapsed:.1f}s)"
                        else:
                            _flush_reason = "sentence_end"
                        seg_text = strip_dsml_markup(normalize_qq_faces(current_segment.strip()))
                        current_segment = ""
                        last_flush_time = time.time()
                        if seg_text:
                            cfg.chat_logger.info(
                                # 2026-07-03: tag 从 STREAM_SEG 改为 STREAM_BUF 表示只缓冲不发送
                                f"[STREAM_BUF] reason={_flush_reason} | "
                                f"len={len(seg_text)} | text='{seg_text[:80]}{'...' if len(seg_text)>80 else ''}'"
                            )
                            turn_recorder.add_step({
                                "kind": "stream_segment",
                                "title": f"流式片段 ({_flush_reason})",
                                "data": {"text": seg_text, "reason": _flush_reason},
                            })
                            # 2026-07-03: 流式不再分段发送,由流结束后 split_at_breath_pauses 统一切段
                            # try:
                            #     await char_chat.send(parse_qq_faces(seg_text))
                            #     sent_count += 1
                            # except Exception as e:
                            #     logger.warning(f"[STREAM] send error: {e}")
                            #     break
            except Exception as e:
                logger.warning(f"[STREAM] streaming error: {e}")
            finally:
                timer_task.cancel()

            # 处理尾部残留内容
            if current_segment.strip():
                seg_text = strip_dsml_markup(normalize_qq_faces(current_segment.strip()))
                if seg_text:
                    # 2026-07-03: 流式不再分段发送,此处不再发送,改为合并到 full_reply 末端
                    full_reply += current_segment
                    cfg.chat_logger.info(
                        f"[STREAM_BUF] tail residual len={len(seg_text)} merged into full_reply"
                    )

            # 2026-07-03: 只做轻量预处理(去 dsml + 规范化 QQ 表情),不调用 clean_chat_output
            # (clean 会把 \n\n 折叠成 \n,导致 split_at_breath_pauses 找不到段落分隔)
            # 2026-07-03 进一步: 先 dedup_consecutive_chunks 删除 LLM 流式复读的整段重复
            # (deepseek 在萌系角色长回复中偶发复读,如把同一段 30+ 字段的输出写两遍)
            _deduped = dedup_consecutive_chunks(full_reply.strip()) if full_reply.strip() else ""
            if _deduped != full_reply.strip():
                cfg.chat_logger.info(
                    f"[DEDUP] 删除复读段: {len(full_reply.strip())} → {len(_deduped)} chars"
                )
            reply = strip_dsml_markup(normalize_qq_faces(_deduped)) if _deduped else None
            turn_recorder.add_step({
                "kind": "reply",
                "title": f"生成回复 ({len(reply) if reply else 0} 字)",
                "data": {"text": (reply or "")[:4000], "stream_chars": len(full_reply or "")},
            })
            _stream_already_sent = sent_count > 0

            # 流式输出清洗后为空，回退到非流式
            if not reply and not _stream_already_sent:
                cfg.chat_logger.warning("[STREAM] 流式输出清洗后为空（可能是工具调用泄露），回退到非流式")
                try:
                    reply = await llm.call_deepseek(effective_prompt, _effective_history, skill_name=active_name)
                except Exception as e:
                    logger.warning(f"[STREAM] fallback error: {e}")
            # 流式输出被截断（已发送部分内容但总回复极短），尝试非流式补救
            elif _stream_already_sent and full_reply.strip() and len(full_reply.strip()) < 20:
                cfg.chat_logger.warning(f"[STREAM] 流式输出疑似截断（len={len(full_reply.strip())}），尝试非流式补救")
                try:
                    reply = await llm.call_deepseek(effective_prompt, _effective_history, skill_name=active_name)
                    if reply and len(reply) > len(full_reply.strip()):
                        _stream_already_sent = False  # 用完整回复替代已发送的截断内容
                except Exception as e:
                    logger.warning(f"[STREAM] truncation fallback error: {e}")
        else:
            # ── 非流式调用（工具在模型手里，由它自己决定调不调）──
            # 2026-09-12: 去掉「关键词 → bot 预搜索 + 收走工具」这条短路。
            # 之前的理由是 deepseek v4 在 thinking 下无视 tool_choice、爱凭记忆瞎答；
            # 现在改为：关键词命中只加一句提示，工具保留给模型自己决定。
            # 若实测发现模型该搜不搜，可再考虑加"事后兜底预搜索"（见 git 历史/对话记录）。
            _force_tools = False
            _prompt_for_tools = _probe_prompt
            try:
                reply = await llm.call_deepseek(
                    _prompt_for_tools, _effective_history,
                    skill_name=active_name,
                    force_tools=_force_tools,
                    disable_tools=False,
                )
            except Exception as e:
                logger.error(f"[CHAT] LLM call failed: {e}")
                reply = None
            finally:
                _timer_cancelled = True
                timer_task.cancel()
            _stream_already_sent = False

        if reply is None:
            reply = "⚠️ LLM 调用失败，请稍后重试或检查 Dashboard 设置。"

        # 规范化 QQ 表情格式 + 清理空白
        # 2026-07-03: 不再调用 clean_chat_output — 它会把 \n\n 折叠成 \n, 破坏后续 split 段落识别
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
            # 2026-07-03: 改用 split_at_breath_pauses 按语义呼吸点切段,
            # 能识别颜文字/emoji/日式句末(⁰̷̴͈う♡ / (´･ω･`) / ‼️) 等自然句末。
            # split_message 仍保留供 web 搜索回复场景使用。
            # 注: 在 split 之前不要调用 clean_chat_output(会把 \n\n 折叠成 \n,破坏段落识别)
            segments = split_at_breath_pauses(reply) or [reply]
            # split 完成后对每段单独 clean(去前后空白、压缩段内多空行)
            segments = [clean_chat_output(s) for s in segments if s.strip()]
            cfg.chat_logger.info(
                f"[REPLY] user={uid} | segments={len(segments)} "
                f"| reply='{reply[:100]}{'...' if len(reply)>100 else ''}'"
            )

            if len(segments) == 1:
                await char_chat.send(parse_qq_faces(segments[0]))
            else:
                sent_count = 0
                try:
                    for i, seg in enumerate(segments):
                        await char_chat.send(parse_qq_faces(seg))
                        sent_count += 1
                        if i < len(segments) - 1:
                            delay = min(0.5 + len(seg) / 300, 2.5)
                            await asyncio.sleep(delay)
                except Exception as e:
                    logger.warning(f"[REPLY] 分段发送中断 ({sent_count}/{len(segments)}): {e}")
                    remaining = "\n\n".join(segments[sent_count:])
                    if remaining.strip():
                        try:
                            await char_chat.send(parse_qq_faces(remaining))
                        except Exception:
                            pass
        else:
            cfg.chat_logger.info(
                f"[STREAM] user={uid} | segments={sent_count} "
                f"| reply='{reply[:100]}{'...' if len(reply)>100 else ''}'"
            )

        # ── 情绪表情（角色级开关）──
        # 2026-09-13: SKILL.md front-matter 写 emoji_images: false 的角色完全不发图。
        _emoji_on = getattr(skill, "emoji_images", True) if skill else True
        if not _emoji_on:
            logger.debug(f"[EMOJI] 角色 {active_name} 已关闭表情包（emoji_images: false）")
        if _emoji_on and reply and reply != "⚠️ LLM 调用失败，请稍后重试或检查 Dashboard 设置。":
            try:
                await emoji_system.maybe_send_emoji(bot, event, text, reply, uid)
            except Exception as e:
                logger.debug(f"[EMOJI] error: {e}")

    # 用户锁释放后清理（防止内存泄漏）
    api_client.release_user_lock(uid)
