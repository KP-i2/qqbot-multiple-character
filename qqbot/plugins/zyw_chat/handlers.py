# 处理器
# ============================================================

zyw_chat = on_message(rule=Rule(_respond_rule), priority=50, block=True)

# --- 命令处理 ---

cmd_reset = on_command("reset", aliases={"重置", "清空记忆"}, rule=to_me(), priority=1, block=True)
cmd_skills = on_command("skills", aliases={"角色", "列表"}, rule=to_me(), priority=1, block=True)
cmd_switch = on_command("switch", aliases={"切换"}, rule=to_me(), priority=1, block=True)
cmd_current = on_command("current", aliases={"当前"}, rule=to_me(), priority=1, block=True)
cmd_reloademoji = on_command("reloademoji", aliases={"重载表情"}, rule=to_me(), priority=1, block=True)


@cmd_reset.handle()
async def handle_reset(event: Event):
    uid = event.get_user_id()
    user_lock = _get_user_lock(uid)
    async with user_lock:
        key = get_history_key(event)
        clear_history(key)
        await cmd_reset.finish("好叭！窝把之前聊的都忘光光啦 (◍•ᴗ•◍)❤ 重新开始叭～")


@cmd_skills.handle()
async def handle_skills(event: Event):
    active = global_active_skill or DEFAULT_SKILL_NAME or ""

    if not ALL_SKILLS:
        await cmd_skills.finish("呜哇！现在窝这里一个角色都没有加载捏 [委屈]")
        return

    lines = ["泥可以选这些角色陪泥聊天哦：\n"]
    for name, skill in ALL_SKILLS.items():
        marker = " 👈 当前" if name == active else ""
        desc = skill.description or "暂无描述"
        ver = f" v{skill.version}" if skill.version else ""
        lines.append(f"  {skill.display_name}({name}){ver} — {desc}{marker}")

    lines.append(f"\n发送 /switch <名字> 就能换人啦～")
    await cmd_skills.finish("\n".join(lines))


@cmd_switch.handle()
async def handle_switch(bot: Bot, event: Event):
    uid = event.get_user_id()

    # 管理员权限检查
    if uid != str(ADMIN_QQ):
        await cmd_switch.finish("欸——这个只有窝才可以操作哦 🙈 对不起嘛！")
        return

    user_lock = _get_user_lock(uid)
    async with user_lock:
        # 提取参数
        text = event.get_plaintext().strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            names = ", ".join(f"{s.display_name}({n})" for n, s in ALL_SKILLS.items())
            await cmd_switch.finish(f"要用这个格式哦：/switch <角色名> ✨\n可选的有：{names} 💕")
            return

        target = parts[1].strip()

        if target not in ALL_SKILLS:
            names = ", ".join(f"{s.display_name}({n})" for n, s in ALL_SKILLS.items())
            await cmd_switch.finish(f"呜呜 '{target}' 这个角色窝找不到捏 [抓狂]\n这些是窝有的：{names} 泥再挑挑？")
            return

        global global_active_skill
        old_skill = global_active_skill or DEFAULT_SKILL_NAME or ""
        global_active_skill = target

        # 异步切换 QQ 头像和昵称
        _safe_create_task(_set_profile(bot, target), f"profile-{target}")

        skill = ALL_SKILLS[target]
        await cmd_switch.finish(
            f"切换成功惹！现在是 {skill.display_name}({target}) 在陪泥啦 🧡🌟\n"
            f"{skill.description or ''}\n"
            f"之后大家聊天都会用这个角色辽～"
        )


@cmd_current.handle()
async def handle_current(event: Event):
    active = global_active_skill or DEFAULT_SKILL_NAME or ""
    skill = ALL_SKILLS.get(active)
    if skill:
        prov = _get_active_provider()
        model_line = f"模型：{prov['model']} ({prov['name']})"
        await cmd_current.finish(
            f"听好惹！现在是 {skill.display_name}({skill.name}) 在陪泥聊天哟💕\n"
            f"{skill.description or '暂无描述'}\n"
            f"版本：{skill.version or '?'} 📱✨\n"
            f"模型：{prov['model']} ({prov['name']}) 🧠💭"
        )
    else:
        await cmd_current.finish("窝还没有给泥准备可以聊天的角色呢 [对手指] 泥想选谁呀？")


@cmd_reloademoji.handle()
async def handle_reloademoji(event: Event):
    uid = event.get_user_id()
    if uid != str(ADMIN_QQ):
        await cmd_reloademoji.finish("这个也是只有窝才能弄的啦 🙈💦")
        return
    _load_emoji_files()
    total_imgs = sum(len(v) for v in _EMOJI_FILES.values())
    emotions = list(_EMOJI_FILES.keys())
    lines = [f"叮咚～窝重新整理了一下表情包包！现在有 {total_imgs} 张图、{len(emotions)} 种情绪辽 🎀✨"]
    for emo in emotions:
        imgs = len(_EMOJI_FILES[emo])
        kws = len(_EMOJI_EMOTIONS.get(emo, []))
        lines.append(f"  {emo}: {imgs} 图片, {kws} 关键词")
    await cmd_reloademoji.finish("\n".join(lines))


# --- QQ 富媒体消息解析 ---

def _parse_rich_message(event: Event) -> str:
    """从消息中提取文本，包括 QQ 小程序 / 分享卡片 / XML 消息的可读内容。"""
    parts = []
    for seg in event.message:
        if seg.type == "text":
            t = seg.data.get("text", "").strip()
            if t:
                parts.append(t)

        elif seg.type == "json":
            data_str = seg.data.get("data", "")
            try:
                data = json.loads(data_str) if isinstance(data_str, str) else data_str
                prompt = data.get("prompt", "")
                if prompt:
                    parts.append(prompt)
                else:
                    # 尝试提取 meta 里的描述
                    meta = data.get("meta", {})
                    desc = ""
                    if isinstance(meta, dict):
                        for v in meta.values():
                            if isinstance(v, dict):
                                desc = v.get("desc", "") or v.get("title", "") or v.get("tag", "")
                                if desc:
                                    break
                    app_name = data.get("app", "")
                    view = data.get("view", "")
                    if desc or app_name:
                        label = f"[分享卡片"
                        if app_name:
                            label += f" ({app_name})"
                        label += "]"
                        parts.append(f"{label} {desc}" if desc else label)
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

        elif seg.type == "xml":
            data_str = seg.data.get("data", "")
            if data_str:
                # 从 XML 中提取标题和描述
                for tag in ("title", "brief", "source"):
                    m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', data_str, re.DOTALL)
                    if m:
                        val = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                        if val and len(val) > 2:
                            parts.append(val)

        elif seg.type == "share":
            title = seg.data.get("title", "")
            url = seg.data.get("url", "")
            content = seg.data.get("content", "")
            if title:
                share_text = f"[分享链接] {title}"
                if content:
                    share_text += f" - {content}"
                if url:
                    share_text += f" {url}"
                parts.append(share_text)

        elif seg.type not in ("at", "image", "face", "record", "video",
                              "rps", "dice", "shake", "poke", "anonymous",
                              "contact", "location", "music", "reply",
                              "forward", "node"):
            # 未知类型，尝试字符串化
            s = str(seg).strip()
            if s and len(s) > 3 and not s.startswith("[CQ:"):
                parts.append(s)

    return " ".join(parts).strip()


# --- 聊天处理 ---

@zyw_chat.handle()
async def handle_chat(bot: Bot, event: Event):
    global global_active_skill  # 允许 switch 子命令修改全局角色
    logger.info(f"[DEBUG] Message received: type={type(event).__name__}, user={event.get_user_id()}")

    # 活跃时间检查
    now = datetime.now()
    if now.hour < ACTIVE_HOURS_START or now.hour > ACTIVE_HOURS_END:
        return  # 静默，不回复

    text = event.get_plaintext().strip()
    # 纯文本为空时，尝试解析 QQ 小程序 / 分享卡片 / XML 消息
    if not text:
        text = _parse_rich_message(event)
    if not text:
        return

    # 过滤掉 @ bot 的部分（群聊）
    if isinstance(event, GroupMessageEvent):
        # 移除 at 段
        clean_parts = []
        for seg in event.message:
            if seg.type == "text":
                clean_parts.append(seg.data.get("text", ""))
            elif seg.type == "at":
                continue
            elif seg.type in ("json", "xml", "share"):
                rich = _parse_rich_message(event)
                if rich and rich not in "".join(clean_parts):
                    clean_parts.append(rich)
            else:
                clean_parts.append(str(seg))
        text = "".join(clean_parts).strip()
        if not text:
            return

    # 手动命令分发（因为群聊 @Bot 后 text 开头有空格，on_command 无法匹配）
    if text.startswith("/") or text.startswith("／"):
        parts = text[1:].strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("reset", "重置", "清空记忆"):
            key = get_history_key(event)
            clear_history(key)
            await zyw_chat.finish("好叭！窝把之前聊的都忘光光啦 (◍•ᴗ•◍)❤ 重新开始叭～")
            return

        if cmd in ("skills", "角色", "列表"):
            active = global_active_skill or DEFAULT_SKILL_NAME or ""
            if not ALL_SKILLS:
                await zyw_chat.finish("呜哇！现在窝这里一个角色都没有加载捏 [委屈]")
                return
            lines = ["泥可以选这些角色陪泥聊天哦：\n"]
            for name, skill in ALL_SKILLS.items():
                marker = " 👈 当前" if name == active else ""
                desc = skill.description or "暂无描述"
                ver = f" v{skill.version}" if skill.version else ""
                lines.append(f"  {skill.display_name}({name}){ver} — {desc}{marker}")
            lines.append(f"\n发送 /switch <名字> 就能换人啦～")
            await zyw_chat.finish("\n".join(lines))
            return

        if cmd in ("switch", "切换"):
            # 管理员权限检查
            if uid != str(ADMIN_QQ):
                await zyw_chat.finish("欸——这个只有窝才可以操作哦 🙈 对不起嘛！")
                return

            if not arg:
                names = ", ".join(f"{s.display_name}({n})" for n, s in ALL_SKILLS.items())
                await zyw_chat.finish(f"要用这个格式哦：/switch <角色名> ✨\n可选的有：{names} 💕")
                return
            if arg not in ALL_SKILLS:
                names = ", ".join(f"{s.display_name}({n})" for n, s in ALL_SKILLS.items())
                await zyw_chat.finish(f"呜呜 '{arg}' 这个角色窝找不到捏 [抓狂]\n这些是窝有的：{names} 泥再挑挑？")
                return

            old_skill = global_active_skill or DEFAULT_SKILL_NAME or ""
            global_active_skill = arg

            # 异步切换 QQ 头像和昵称
            _safe_create_task(_set_profile(bot, arg), f"profile-{arg}")

            skill = ALL_SKILLS[arg]
            await zyw_chat.finish(
                f"切换成功惹！现在是 {skill.display_name}({arg}) 在陪泥啦 🧡🌟\n"
                f"{skill.description or ''}\n"
                f"之后大家聊天都会用这个角色辽～"
            )
            return

        if cmd in ("current", "当前"):
            active = global_active_skill or DEFAULT_SKILL_NAME or ""
            skill = ALL_SKILLS.get(active)
            if skill:
                # 获取当前 LLM provider 信息
                prov = _get_active_provider()
                await zyw_chat.finish(
                    f"听好惹！现在是 {skill.display_name}({skill.name}) 在陪泥聊天哟💕\n"
                    f"{skill.description or '暂无描述'}\n"
                    f"版本：{skill.version or '?'} 📱✨\n"
                    f"模型：{prov['model']} ({prov['name']}) 🧠💭"
                )
            else:
                await zyw_chat.finish("窝还没有给泥准备可以聊天的角色呢 [对手指] 泥想选谁呀？")
            return

        if cmd in ("reloademoji", "重载表情"):
            if event.get_user_id() != str(ADMIN_QQ):
                await zyw_chat.finish("这个也是只有窝才能弄的啦 🙈💦")
                return
            _load_emoji_files()
            total_imgs = sum(len(v) for v in _EMOJI_FILES.values())
            emotions = list(_EMOJI_FILES.keys())
            lines = [f"叮咚～窝重新整理了一下表情包包！现在有 {total_imgs} 张图、{len(emotions)} 种情绪辽 🎀✨"]
            for emo in emotions:
                imgs = len(_EMOJI_FILES[emo])
                kws = len(_EMOJI_EMOTIONS.get(emo, []))
                lines.append(f"  {emo}: {imgs} 图片, {kws} 关键词")
            await zyw_chat.finish("\n".join(lines))
            return

    uid = event.get_user_id()
    history_key = get_history_key(event)

    # ── URL 提取与异步抓取（在等待用户锁期间并行执行） ──
    _msg_urls = _extract_urls(text)
    _url_fetch_task = None
    if _msg_urls:
        chat_logger.info(f"[URL] 检测到 URL: {_msg_urls}")
        _url_fetch_task = asyncio.create_task(_process_message_urls(_msg_urls))

    # ── 用户级排队：同一用户的消息顺序处理，避免并发调 API ──
    user_lock = _get_user_lock(uid)
    if user_lock.locked():
        chat_logger.info(f"[QUEUE] user={uid} 正在排队等待上一条消息处理完成")
    async with user_lock:

        # ── 提取说话人身份（优先QQ昵称，其次群名片）──
        sender_name = ""
        if isinstance(event, GroupMessageEvent):
            sender_name = getattr(event.sender, "nickname", "") or getattr(event.sender, "card", "") or ""
        elif isinstance(event, PrivateMessageEvent):
            sender_name = getattr(event.sender, "nickname", "") or ""
        sender_name = sender_name.strip()

        # 获取当前全局角色的 system prompt
        active_name = global_active_skill or DEFAULT_SKILL_NAME or ""
        skill = ALL_SKILLS.get(active_name)
        if not skill:
            await zyw_chat.finish("窝懵惹！没有角色在和泥聊天呢 [委屈] 先用 /skills 看看有谁可以陪泥叭～")
            return

        # 注入说话人身份到 system prompt
        effective_prompt = skill.system_prompt
        context_parts = []
        if sender_name:
            context_parts.append(f"当前和你聊天的人叫「{sender_name}」，你可以在回复中自然地称呼对方，但不要刻意重复名字")

        # ── 用户画像注入 ──
        profile_text = _get_profile_summary(uid, active_name)
        if profile_text:
            context_parts.append(f"用户画像（{profile_text}）")

        # ── 上下文风格自适应 ──
        # 根据用户消息特征和对话历史动态调整回复风格
        style_hints = []

        # 消息长度 → 回复详略
        if len(text) <= 8:
            style_hints.append("对方消息很短，回复应简洁自然，一两句话即可")
        elif len(text) > 200:
            style_hints.append("对方说了很多，可以适当详细回应，但不要长篇大论")

        # 提问检测 → 回答倾向
        if re.search(r'[？?]', text):
            style_hints.append("对方在提问或寻求回应，请给出有内容的回答")

        # 群聊 vs 私聊氛围
        if isinstance(event, GroupMessageEvent):
            style_hints.append("这是群聊场景，回复应轻松简短，像朋友闲聊")
        else:
            style_hints.append("这是私聊，可以更亲密、自然地交流")

        # 对话节奏检测：最近几轮如果都是短消息，保持简洁
        recent = conversation_histories[history_key][-4:]
        user_msgs = [m["content"] for m in recent if m["role"] == "user"]
        if user_msgs and all(len(m) <= 15 for m in user_msgs):
            style_hints.append("对话节奏很快（对方一直发短消息），保持简短回复")

        # URL 分享 → 忽略消息长度，给出有内容的回应
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
                    chat_logger.info(f"[URL] 已注入 URL 内容到上下文 ({len(_url_context)} 字符)")
            except Exception as e:
                logger.warning(f"[URL] 获取 URL 内容失败: {e}")

        if context_parts:
            effective_prompt = effective_prompt + "\n\n[" + "]\n[".join(context_parts) + "]"

        # 添加到历史
        add_to_history(history_key, "user", text)

        # 记录收到的消息
        chat_type = "GROUP" if isinstance(event, GroupMessageEvent) else "PRIVATE"
        gid = getattr(event, "group_id", "-")
        chat_logger.info(
            f"[MSG] {chat_type} | group={gid} | user={uid} | sender={sender_name or 'N/A'} "
            f"| skill={active_name} | text='{text[:80]}{'...' if len(text)>80 else ''}'"
        )

        # 调用 LLM（传入当前角色名以启用语料库搜索）
        # ── 等待提示 + 流式输出 ──
        _thinking_sent = False
        _timer_cancelled = False

        async def _thinking_timer():
            nonlocal _thinking_sent
            await asyncio.sleep(40)
            if not _timer_cancelled:
                try:
                    await zyw_chat.send(Message("唔…让窝想想哦 (´•ω•̥`)💭"))
                    _thinking_sent = True
                except Exception:
                    pass

        timer_task = asyncio.create_task(_thinking_timer())

        # ── 流式决策：技能激活时先探测是否需要工具，不需要则走流式 ──
        _probe_skipped = False
        if active_name and _STREAM_ENABLED:
            # 关键词快速通道：含搜索意图时跳过 probe，强制走工具调用
            if _has_search_intent(text):
                use_stream = False
                chat_logger.info(f"[PROBE] 检测到搜索意图关键词，跳过探测，直接走工具调用")
            else:
                needs_tools = await _probe_tool_usage(
                    effective_prompt, conversation_histories[history_key], active_name
                )
                if not needs_tools:
                    use_stream = True
                    _probe_skipped = True
                    chat_logger.info("[PROBE] 无需工具，切换流式输出")
                else:
                    use_stream = False
        else:
            use_stream = _STREAM_ENABLED and not (WEB_SEARCH_ENABLED and _SEARCH_AVAILABLE) and not active_name

        if use_stream:
            # ── 流式输出：边生成边发送，按句末自然断点分段 ──
            _sentence_end = set("。！？\n!?")
            full_reply = ""
            current_segment = ""
            sent_count = 0
            last_flush_time = time.time()
            _stream_provider = _get_active_provider()

            try:
                async for token in _api_request_stream({
                    "model": _stream_provider["model"],
                    "reasoning_effort": "high",
                    "temperature": 0.95,
                    "top_p": 0.9,
                    "max_tokens": 2048,
                    "frequency_penalty": 0.3,
                    "presence_penalty": 0.2,
                    "messages": [{"role": "system", "content": effective_prompt}]
                                + conversation_histories[history_key],
                }):
                    # 首个 token 到达，取消思考提示定时器
                    if not _timer_cancelled:
                        _timer_cancelled = True
                        timer_task.cancel()

                    current_segment += token
                    full_reply += token
                    now = time.time()

                    should_flush = (
                        (len(current_segment) >= _STREAM_FLUSH_CHARS
                         and current_segment[-1] in _sentence_end)
                        or len(current_segment) >= _STREAM_MAX_FLUSH_SIZE
                    )

                    if should_flush:
                        _flush_reason = "max_size" if len(current_segment) >= _STREAM_MAX_FLUSH_SIZE else "sentence_end"
                        seg_text = _strip_dsml_markup(_normalize_qq_faces(current_segment.strip()))
                        current_segment = ""
                        last_flush_time = time.time()
                        if seg_text:
                            chat_logger.info(
                                f"[STREAM_SEG] reason={_flush_reason} | "
                                f"len={len(seg_text)} | text='{seg_text[:80]}{'...' if len(seg_text)>80 else ''}'"
                            )
                            try:
                                await zyw_chat.send(_parse_qq_faces(seg_text))
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
                seg_text = _strip_dsml_markup(_normalize_qq_faces(current_segment.strip()))
                if seg_text:
                    try:
                        await zyw_chat.send(_parse_qq_faces(seg_text))
                        sent_count += 1
                    except Exception:
                        pass

            reply = _strip_dsml_markup(full_reply.strip()) if full_reply.strip() else None
            # 流式已经发送完毕，标记跳过后续发送
            _stream_already_sent = sent_count > 0

            # 流式输出全是工具调用标签（清洗后为空），回退到非流式调用
            if not reply and not _stream_already_sent:
                chat_logger.warning("[STREAM] 流式输出清洗后为空（可能是工具调用泄露），回退到非流式")
                try:
                    reply = await call_deepseek(effective_prompt, conversation_histories[history_key], skill_name=active_name)
                except Exception as e:
                    logger.warning(f"[STREAM] fallback error: {e}")
        else:
            # ── 非流式调用（工具调用场景或流式被禁用） ──
            try:
                reply = await call_deepseek(effective_prompt, conversation_histories[history_key], skill_name=active_name)
            finally:
                _timer_cancelled = True
                timer_task.cancel()
            _stream_already_sent = False

        if reply is None:
            reply = "啊啊窝的脑袋当机惹 [抓狂] 让窝休息一下下再来陪泥聊天好不好 ꒰ᐢ⸝⸝⸝⸝⸝⸝ᐢ꒱"

        # 规范化 QQ 表情格式（流式路径已在发送时做过，这里处理非流式和兜底）
        if not use_stream:
            reply = _normalize_qq_faces(reply)

        # 添加到历史
        add_to_history(history_key, "assistant", reply)
        _save_histories()  # 每轮对话结束后节流保存

        # ── 用户画像更新触发 ──
        _profile_turn_count[uid] = _profile_turn_count.get(uid, 0) + 1
        last_update = _profile_last_update.get(uid, 0)
        if (_profile_turn_count[uid] >= _PROFILE_UPDATE_MIN_TURNS
                and time.time() - last_update > _PROFILE_UPDATE_INTERVAL):
            _profile_turn_count[uid] = 0
            _profile_last_update[uid] = time.time()
            chat_logger.info(f"[PROFILE] 触发画像提取 uid={uid}, skill={active_name}")
            _safe_create_task(
                _extract_user_profile(uid, history_key, active_name),
                f"profile-{uid}",
            )

        # ── 非流式分段发送（最多3段，段间自然延迟，带中断保护） ──
        if not _stream_already_sent:
            segments = _split_message(reply)
            chat_logger.info(
                f"[REPLY] user={uid} | segments={len(segments)} "
                f"| reply='{reply[:100]}{'...' if len(reply)>100 else ''}'"
            )

            if len(segments) == 1:
                await zyw_chat.send(_parse_qq_faces(segments[0]))
            else:
                sent_count = 0
                try:
                    for i, seg in enumerate(segments):
                        await zyw_chat.send(_parse_qq_faces(seg))
                        sent_count += 1
                        if i < len(segments) - 1:
                            delay = min(0.5 + len(seg) / 300, 2.5)
                            await asyncio.sleep(delay)
                except Exception as e:
                    logger.warning(f"[REPLY] 分段发送中断 ({sent_count}/{len(segments)}): {e}")
                    remaining = "\n\n".join(segments[sent_count:])
                    if remaining.strip():
                        try:
                            await zyw_chat.send(_parse_qq_faces(remaining))
                        except Exception:
                            pass
        else:
            chat_logger.info(
                f"[STREAM] user={uid} | segments={sent_count} "
                f"| reply='{reply[:100]}{'...' if len(reply)>100 else ''}'"
            )

        # ── 情绪表情：回复发送后，根据对话情绪概率附带表情 ──
        if reply and reply != "啊啊窝的脑袋当机惹 [抓狂] 让窝休息一下下再来陪泥聊天好不好 ꒰ᐢ⸝⸝⸝⸝⸝⸝ᐢ꒱":
            try:
                await _maybe_send_emoji(text, reply, uid)
            except Exception as e:
                logger.debug(f"[EMOJI] error: {e}")


# ============================================================
