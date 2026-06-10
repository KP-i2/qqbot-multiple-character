"""
zyw 人格 QQ 聊天插件 (多 Skill 版)
支持多个角色 Skill 目录，可通过命令切换
"""

import json
import math
import os
import re
import time
import base64
import asyncio
import logging
from datetime import datetime
import io
from pathlib import Path
from collections import defaultdict
from typing import Optional
from dataclasses import dataclass, field

import httpx
import nonebot
from nonebot import on_message, on_command, get_driver, logger
from nonebot.message import event_preprocessor

from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    Message,
    MessageSegment,
    GroupMessageEvent,
    PrivateMessageEvent,
)
from nonebot.rule import Rule, to_me

# ============================================================

from .config import *
from .search import *
from .handlers import *

# 头像管理
# ============================================================

MAX_AVATAR_SIZE = 640

def _get_avatar_path(skill_name: str) -> Optional[Path]:
    """从 photo/{skill_name}/ 目录找到原始图片路径（不处理格式，直接用原图）"""
    skill_photo_dir = PHOTO_DIR / skill_name
    if not skill_photo_dir.exists():
        return None
    for ext in (".jpg", ".jpeg", ".png"):
        for f in skill_photo_dir.iterdir():
            if f.suffix.lower() == ext and not f.name.startswith("_avatar"):
                logger.info(f"Avatar found: {skill_name} -> {f.name} ({f.stat().st_size / 1024:.0f}KB)")
                return f
    return None


async def _set_profile(bot: Bot, skill_name: str):
    """尝试切换 QQ 昵称和头像（NTQQ 可能不支持，优雅降级）"""
    skill = ALL_SKILLS.get(skill_name)
    nickname = skill.display_name if skill else skill_name

    logger.info(f"[_set_profile] Attempting: {skill_name} -> {nickname}")

    # 昵称
    try:
        result = await bot.call_api("set_qq_profile", nickname=nickname)
        if isinstance(result, dict) and result.get("result", -1) == 0:
            logger.info(f"[_set_profile] Nickname OK: {nickname}")
        else:
            logger.warning(f"[_set_profile] Nickname rejected (NTQQ limitation): {result}")
    except Exception as e:
        logger.warning(f"[_set_profile] Nickname failed (NTQQ limitation): {e}")

    # 头像
    avatar_path = _get_avatar_path(skill_name)
    if avatar_path:
        try:
            import base64 as b64mod
            raw = avatar_path.read_bytes()
            b64 = b64mod.b64encode(raw).decode()
            result = await bot.call_api("set_qq_avatar", file=f"base64://{b64}")
            logger.info(f"[_set_profile] Avatar OK")
        except Exception as e:
            logger.warning(f"[_set_profile] Avatar failed (NTQQ limitation): {e}")
    else:
        logger.warning(f"[_set_profile] No avatar found for {skill_name}")

# ============================================================
# Skill 管理
# ============================================================

@dataclass
class Skill:
    """一个角色 Skill"""
    name: str
    path: Path
    system_prompt: str
    display_name: str = ""
    description: str = ""
    version: str = ""
    prompt_size: int = 0


def load_skill(skill_path: Path) -> Optional[Skill]:
    """从目录加载一个 Skill"""
    name = skill_path.name
    skill_md = skill_path / "SKILL.md"
    persona_md = skill_path / "persona.md"
    work_md = skill_path / "work.md"

    if not persona_md.exists() and not work_md.exists():
        return None

    # 解析 SKILL.md 获取元数据
    description = ""
    version = ""
    display_name = ""
    if skill_md.exists():
        content = skill_md.read_text(encoding="utf-8")
        # 简单解析 frontmatter
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                fm = content[3:end].strip()
                for line in fm.split("\n"):
                    if line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("version:"):
                        version = line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("display_name:"):
                        display_name = line.split(":", 1)[1].strip().strip('"').strip("'")
    # display_name 默认用目录名
    if not display_name:
        display_name = name

    # 构建 system prompt
    role_instruction = (
        "# 角色扮演指令\n"
        "你现在要完全扮演以下角色。你不是 AI 助手，你就是这个人。\n"
        "始终保持角色的说话方式、态度和价值观。不要跳出角色，不要说'作为一个AI'之类的话。\n"
        "用中文回复，口语化，像在微博/微信聊天一样自然。\n\n"
        "# 工具使用原则（极其重要）\n"
        "你有两个搜索工具可以使用：web_search（联网搜索）和 search_corpus（语料库搜索）。\n"
        "- 当用户问到你不确定的事实、最新事件、专业领域知识、时事热点时，必须主动使用 web_search 搜索\n"
        "- 当用户明确提到「查」「搜」「找」「看看」「帮我查」「帮我搜」「了解一下」等查询意图时，必须使用 web_search\n"
        "- 当用户提到具体的团体名、人名、作品名、活动名等你不完全确定的专有名词时，必须先用 web_search 查证\n"
        "- 当用户问到与角色背景、经历、设定相关的问题时，应使用 search_corpus 查询角色资料\n"
        "- 绝对不要编造搜索结果的口吻（如'搜了一下''查到了'），如果没调用搜索工具就不能假装有搜索结果\n"
        "- 宁可多搜一次也不要编造信息，保持回答的真实性和角色一致性\n"
        "- 搜索结果要自然融入回复，不要暴露搜索过程\n\n"
        "# 搜索查询优化（极其重要）\n"
        "生成搜索查询时遵循以下规则：\n"
        "- 如果用户提到具体名称（团体名/人名/作品名），直接搜该名称，不要加修饰词\n"
        "- 如果用户问「本周/最近/今天」的活动，查询中要包含时间词（如「2026年6月 演出」）\n"
        "- 如果用户问的是小众领域（如地下偶像），优先搜微博（中文社区更活跃）\n"
        "- 搜索词尽量简短，只写核心名称（如「阵雨电台」而非「阵雨电台 地下偶像 介绍」）\n"
        "- 不要加「官方」「是谁」「介绍」等通用修饰词，微博搜索对短关键词效果更好\n"
        "- 如果用户没说具体名称，先搜大类（如「地下偶像 演出 本周」），再根据结果搜具体名称\n"
        "# QQ表情使用\n"
        "你可以在回复中自然地使用QQ表情，格式为方括号包裹表情名。"
        "常用表情：[微笑] [撇嘴] [色] [发呆] [得意] [流泪] [害羞] [闭嘴] [睡] [大哭] "
        "[尴尬] [发怒] [调皮] [呲牙] [惊讶] [难过] [酷] [冷汗] [抓狂] [吐] [偷笑] [可爱] "
        "[白眼] [傲慢] [饥饿] [困] [惊恐] [流汗] [憨笑] [悠闲] [奋斗] [咒骂] [疑问] "
        "[嘘] [晕] [折磨] [衰] [骷髅] [敲打] [再见] [擦汗] [抠鼻] [鼓掌] [糗大了] "
        "[坏笑] [左哼哼] [右哼哼] [哈欠] [鄙视] [委屈] [快哭了] [阴险] [亲亲] [吓] "
        "[可怜] [菜刀] [西瓜] [啤酒] [篮球] [乒乓] [咖啡] [饭] [猪头] [玫瑰] [凋谢] "
        "[示爱] [爱心] [拥抱] [强] [弱] [握手] [胜利] [抱拳] [勾引] [拳头] [差劲] "
        "[爱你] [NO] [OK] [转圈] [磕头] [回头] [跳绳] [挥手] [激动] [街舞] [献吻] "
        "[左太极] [右太极] [doge] [捂脸] [笑哭] [嘿哈] [捂嘴笑] [思考] [泪奔] [笑哭] "
        "不要每句话都加，适度使用，符合角色性格和语境。"
        "如果角色性格活泼可以多用，如果角色沉稳内敛则少用或不用。\n"
    )
    parts = [role_instruction]

    if persona_md.exists():
        parts.append(f"# 角色人格\n{persona_md.read_text(encoding='utf-8')}")
    if work_md.exists():
        parts.append(f"# 工作能力\n{work_md.read_text(encoding='utf-8')}")

    # 加载额外参考文件（如 starwink.md 等团体背景）
    for extra_file in sorted(skill_path.glob("*.md")):
        ename = extra_file.name
        if ename in ("SKILL.md", "persona.md", "work.md"):
            continue  # 已加载
        parts.append(f"# 附加参考：{ename}\n{extra_file.read_text(encoding='utf-8')}")

    system_prompt = "\n\n---\n\n".join(parts)

    return Skill(
        name=name,
        path=skill_path,
        system_prompt=system_prompt,
        display_name=display_name,
        description=description,
        version=version,
        prompt_size=len(system_prompt),
    )


def load_all_skills() -> dict[str, Skill]:
    """加载 skills/ 目录下所有角色"""
    skills = {}
    if not SKILLS_DIR.exists():
        logger.warning(f"Skills directory not found: {SKILLS_DIR}")
        return skills

    for d in sorted(SKILLS_DIR.iterdir()):
        if d.is_dir() and not d.name.startswith((".", "_")):
            skill = load_skill(d)
            if skill:
                skills[skill.name] = skill
                logger.info(f"  [OK] Skill '{skill.name}' loaded "
                           f"(display: {skill.display_name}, "
                           f"prompt: {len(skill.system_prompt)} chars, "
                           f"v{skill.version})")
            else:
                logger.info(f"  [SKIP] '{d.name}' - no persona.md or work.md")

    return skills


# 启动时加载所有 Skill
logger.info(f"Loading skills from: {SKILLS_DIR}")
ALL_SKILLS = load_all_skills()
logger.info(f"Total skills loaded: {len(ALL_SKILLS)}")

# 默认 skill：优先使用 .env 中配置的 DEFAULT_SKILL，否则取目录排序第一个
if DEFAULT_SKILL_NAME_CONFIG and DEFAULT_SKILL_NAME_CONFIG in ALL_SKILLS:
    DEFAULT_SKILL_NAME = DEFAULT_SKILL_NAME_CONFIG
    logger.info(f"默认角色（配置指定）：{DEFAULT_SKILL_NAME}")
else:
    DEFAULT_SKILL_NAME = next(iter(ALL_SKILLS), None) if ALL_SKILLS else None
    if DEFAULT_SKILL_NAME_CONFIG:
        logger.warning(f"配置的默认角色 '{DEFAULT_SKILL_NAME_CONFIG}' 不存在，使用：{DEFAULT_SKILL_NAME}")
    else:
        logger.info(f"默认角色（自动选择）：{DEFAULT_SKILL_NAME}")

# ── Skill 热重载 ──
RELOAD_TRIGGER = QQBOT_DIR.parent / ".reload_skills_trigger"

def reload_skills():
    """重新加载所有 Skill（热重载）"""
    global ALL_SKILLS, DEFAULT_SKILL_NAME
    old_names = set(ALL_SKILLS.keys())
    ALL_SKILLS = load_all_skills()
    new_names = set(ALL_SKILLS.keys())
    # 重新确定默认角色（仅用于首次启动，热重载不影响当前活跃角色）
    if DEFAULT_SKILL_NAME_CONFIG and DEFAULT_SKILL_NAME_CONFIG in ALL_SKILLS:
        DEFAULT_SKILL_NAME = DEFAULT_SKILL_NAME_CONFIG
    else:
        DEFAULT_SKILL_NAME = next(iter(ALL_SKILLS), None) if ALL_SKILLS else None
    added = new_names - old_names
    removed = old_names - new_names
    logger.info(f"[reload] Skills reloaded: {len(ALL_SKILLS)} total, +{len(added)} -{len(removed)}, default={DEFAULT_SKILL_NAME}")
    # 清理触发文件
    RELOAD_TRIGGER.unlink(missing_ok=True)


@event_preprocessor
async def _check_reload_trigger():
    """在所有事件（包括命令）处理前检查是否需要热重载 Skill 或设置"""
    if RELOAD_TRIGGER.exists():
        reload_skills()
    if _SETTINGS_RELOAD_TRIGGER.exists():
        _load_runtime_config()
        _apply_runtime_config()
        _SETTINGS_RELOAD_TRIGGER.unlink(missing_ok=True)
        logger.info("[settings] Runtime settings reloaded from dashboard")


# ============================================================
# 对话状态管理
# ============================================================

# 管理员的当前 skill (全局角色，所有用户共用)
user_active_skill: dict[str, str] = defaultdict(lambda: DEFAULT_SKILL_NAME or "")
# 对话历史 (key = 用户/群 + 角色名，每个用户在每个角色下独立对话)
conversation_histories: dict[str, list[dict]] = defaultdict(list)
_history_timestamps: dict[str, float] = {}  # key → last access time
_HISTORY_TTL = 3600 * 6  # 6小时无活动自动清理

# ── 对话历史持久化 ──
_HISTORY_DIR = QQBOT_DIR / "data"
_HISTORY_DIR.mkdir(exist_ok=True)
_HISTORY_FILE = _HISTORY_DIR / "conversation_histories.json"
_HISTORY_SAVE_INTERVAL = 60  # 最多每 60 秒存一次
_history_dirty = False
_history_last_save = 0.0

# ── 用户画像（User Profile） ──
_PROFILE_DIR = _HISTORY_DIR / "user_profiles"
_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
_PROFILE_UPDATE_INTERVAL = 3600   # 至少 1 小时更新一次
_PROFILE_UPDATE_MIN_TURNS = 10    # 累积 10 轮对话才触发
_profile_last_update: dict[str, float] = {}
_profile_turn_count: dict[str, int] = defaultdict(int)
_profile_cache: dict[str, dict] = {}
_profile_semaphore = asyncio.Semaphore(2)  # 画像提取最多 2 个并发


def _load_histories():
    """启动时从磁盘恢复对话历史"""
    global _history_last_save
    if not _HISTORY_FILE.exists():
        return
    try:
        data = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        for key, entry in data.items():
            if isinstance(entry, dict) and "messages" in entry:
                conversation_histories[key] = entry["messages"]
                _history_timestamps[key] = entry.get("timestamp", 0)
        _history_last_save = time.time()
        logger.info(f"[history] Restored {len(data)} conversations from disk")
    except Exception as e:
        logger.warning(f"[history] Failed to load histories: {e}")


def _save_histories(force: bool = False):
    """将对话历史写入磁盘（带节流，最多每 60 秒写一次）"""
    global _history_dirty, _history_last_save
    now = time.time()
    if not force and (not _history_dirty or now - _history_last_save < _HISTORY_SAVE_INTERVAL):
        return
    try:
        data = {}
        for key in list(conversation_histories.keys()):
            msgs = conversation_histories[key]
            if msgs:  # 只保存非空历史
                data[key] = {
                    "messages": msgs,
                    "timestamp": _history_timestamps.get(key, 0),
                }
        _HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _history_dirty = False
        _history_last_save = now
    except Exception as e:
        logger.warning(f"[history] Failed to save histories: {e}")


# 启动时恢复历史
_load_histories()

# 当前全局活跃角色 (管理员切换后影响所有用户)
global_active_skill: str = DEFAULT_SKILL_NAME or ""


def get_history_key(event: Event) -> str:
    skill_name = global_active_skill or DEFAULT_SKILL_NAME or "default"
    if isinstance(event, GroupMessageEvent):
        return f"group_{event.group_id}_{event.user_id}_{skill_name}"
    elif isinstance(event, PrivateMessageEvent):
        return f"user_{event.user_id}_{skill_name}"
    return f"unknown_{event.get_user_id()}_{skill_name}"


def add_to_history(key: str, role: str, content: str):
    global _history_dirty
    history = conversation_histories[key]
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY_MESSAGES:
        history[:] = history[-MAX_HISTORY_MESSAGES:]
    _history_timestamps[key] = time.time()
    _history_dirty = True


async def _periodic_history_cleanup():
    """每30分钟清理一次过期对话历史"""
    while True:
        await asyncio.sleep(1800)  # 30分钟
        now = time.time()
        expired = [k for k, t in _history_timestamps.items() if now - t > _HISTORY_TTL]
        for k in expired:
            conversation_histories.pop(k, None)
            _history_timestamps.pop(k, None)
        if expired:
            logger.info(f"[cleanup] Removed {len(expired)} expired conversation histories")
            _save_histories(force=True)


def clear_history(key: str):
    global _history_dirty
    conversation_histories.pop(key, None)
    _history_timestamps.pop(key, None)
    _history_dirty = True


# ============================================================
# 用户画像（User Profile）
# ============================================================

def _load_user_profile(uid: str) -> Optional[dict]:
    """加载用户画像，优先内存缓存"""
    if uid in _profile_cache:
        return _profile_cache[uid]
    path = _PROFILE_DIR / f"{uid}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _profile_cache[uid] = data
        return data
    except Exception as e:
        logger.warning(f"[PROFILE] 加载失败 uid={uid}: {e}")
        return None


def _save_user_profile(uid: str, profile: dict):
    """保存用户画像到磁盘和内存缓存"""
    try:
        profile["updated_at"] = datetime.now().isoformat()
        path = _PROFILE_DIR / f"{uid}.json"
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        _profile_cache[uid] = profile
        chat_logger.info(f"[PROFILE] 已保存 uid={uid}, summary={profile.get('profile_summary', '')[:60]}")
    except Exception as e:
        logger.warning(f"[PROFILE] 保存失败 uid={uid}: {e}")


async def _extract_user_profile(uid: str, history_key: str, skill_name: str):
    """后台任务：从对话历史中提取用户画像（增量更新）"""
    try:
        await asyncio.wait_for(_profile_semaphore.acquire(), timeout=30)
    except asyncio.TimeoutError:
        chat_logger.warning(f"[PROFILE] 信号量超时 uid={uid}，跳过")
        return

    try:
        # 取最近 10 轮对话
        history = conversation_histories.get(history_key, [])
        recent = history[-20:]
        if len(recent) < 4:
            return

        # 加载现有画像
        existing = _load_user_profile(uid) or {}
        existing_summary = existing.get("profile_summary", "")
        existing_per_skill = existing.get("per_skill", {}).get(skill_name, {})

        # 构建提取 prompt
        extraction_prompt = (
            "你是用户画像分析助手。从对话中提取用户特征。\n"
            "输出严格 JSON（不要 markdown 代码块）：\n"
            '{"nickname":"用户昵称","interests":["兴趣1","兴趣2"],'
            '"style":"交互风格","notes":"其他特征"}\n'
            "只输出 JSON，不要其他内容。"
        )

        user_content = json.dumps(recent, ensure_ascii=False)
        if existing_summary:
            user_content = f"【已有画像】{existing_summary}\n\n【新对话】{user_content}"

        payload = {
            "model": DEEPSEEK_SEARCH_MODEL,
            "temperature": 0.3,
            "max_tokens": 512,
            "messages": [
                {"role": "system", "content": extraction_prompt},
                {"role": "user", "content": user_content},
            ],
        }

        data = await _api_request(payload, _get_provider("deepseek"))
        if data is None:
            return

        raw_content = data["choices"][0]["message"]["content"].strip()
        # 清洗可能的 markdown 代码块
        raw_content = re.sub(r'^```(?:json)?\s*', '', raw_content)
        raw_content = re.sub(r'\s*```$', '', raw_content)

        try:
            extracted = json.loads(raw_content)
        except json.JSONDecodeError:
            chat_logger.warning(f"[PROFILE] JSON 解析失败 uid={uid}: {raw_content[:100]}")
            return

        # 合并生成摘要
        interests = extracted.get("interests", [])
        style = extracted.get("style", "")
        notes = extracted.get("notes", "")
        nickname = extracted.get("nickname", "")

        summary_parts = []
        if interests:
            summary_parts.append(f"兴趣：{'、'.join(interests[:5])}")
        if style:
            summary_parts.append(style)
        if notes:
            summary_parts.append(notes)

        # 更新 per_skill
        skill_data = dict(existing_per_skill)
        skill_data["turns"] = skill_data.get("turns", 0) + _profile_turn_count.get(uid, 0)
        skill_data["last_interaction"] = datetime.now().isoformat()
        topics_from_interests = interests[:5] if interests else skill_data.get("topics", [])
        skill_data["topics"] = topics_from_interests

        all_per_skill = dict(existing.get("per_skill", {}))
        all_per_skill[skill_name] = skill_data

        # 组装最终画像
        profile = {
            "uid": uid,
            "nickname": nickname or existing.get("nickname", ""),
            "profile_summary": "，".join(summary_parts) if summary_parts else existing_summary,
            "total_turns": existing.get("total_turns", 0) + _profile_turn_count.get(uid, 0),
            "per_skill": all_per_skill,
        }

        _save_user_profile(uid, profile)

    except Exception as e:
        logger.warning(f"[PROFILE] 提取异常 uid={uid}: {e}")
    finally:
        _profile_semaphore.release()


def _get_profile_summary(uid: str, skill_name: str) -> str:
    """获取用户画像摘要文本，用于注入 system prompt"""
    profile = _load_user_profile(uid)
    if not profile:
        return ""
    summary = profile.get("profile_summary", "")
    if not summary:
        return ""
    nickname = profile.get("nickname", "")
    per_skill = profile.get("per_skill", {}).get(skill_name, {})
    topics = per_skill.get("topics", [])
    display_name = nickname or uid
    text = f"{display_name}：{summary}"
    if topics:
        text += f"，常聊话题：{'、'.join(topics[:5])}"
    return text


# ============================================================

# 消息分段
# ============================================================

_SPLIT_THRESHOLD = 120   # 超过此字符数才分段
_SPLIT_MAX_SEGMENTS = 3   # 最多分几段
_SPLIT_MIN_SEGMENT = 40   # 每段最少字符数


def _split_message(text: str) -> list[str]:
    """将长回复拆分为最多 _SPLIT_MAX_SEGMENTS 段。
    优先按空行分段，其次按句号/换行分段。
    """
    text = text.strip()
    if len(text) <= _SPLIT_THRESHOLD:
        return [text]

    # 1) 先按双换行 / 空行拆块
    blocks = [b.strip() for b in re.split(r'\n\s*\n', text) if b.strip()]

    if len(blocks) <= _SPLIT_MAX_SEGMENTS:
        # 块数刚好够用，合并太短的块
        segments = []
        for b in blocks:
            if segments and len(segments[-1]) < _SPLIT_MIN_SEGMENT:
                segments[-1] = segments[-1] + "\n\n" + b
            else:
                segments.append(b)
        return segments[:_SPLIT_MAX_SEGMENTS]

    # 2) 块数太多，需要合并到 _SPLIT_MAX_SEGMENTS 段
    segments = []
    current = ""
    per_seg = len(text) // _SPLIT_MAX_SEGMENTS

    for i, block in enumerate(blocks):
        if current:
            current += "\n\n" + block
        else:
            current = block

        # 当前段够长 或 是最后一块 → 切段
        is_last = (i == len(blocks) - 1)
        remaining_segments = _SPLIT_MAX_SEGMENTS - len(segments) - 1
        if is_last or (len(current) >= per_seg and remaining_segments > 0):
            segments.append(current)
            current = ""

    # 兜底：如果还有残余，追加到最后一段
    if current and segments:
        segments[-1] += "\n\n" + current
    elif current:
        segments.append(current)

    return segments[:_SPLIT_MAX_SEGMENTS]


# ============================================================
# QQ 表情后处理
# ============================================================

# 常见 QQ 表情别名映射 → 标准名
_QQ_FACE_ALIASES: dict[str, str] = {
    "笑脸": "微笑", "微笑": "微笑",
    "撇嘴": "撇嘴", "嘴巴": "撇嘴",
    "色": "色", "色眯眯": "色",
    "发呆": "发呆", "懵": "发呆",
    "得意": "得意", "酷": "酷",
    "流泪": "流泪", "哭": "流泪", "大哭": "大哭",
    "害羞": "害羞", "脸红": "害羞",
    "闭嘴": "闭嘴", "嘘": "嘘",
    "睡": "睡", "睡觉": "睡",
    "尴尬": "尴尬",
    "发怒": "发怒", "生气": "发怒", "怒": "发怒",
    "调皮": "调皮", "吐舌": "调皮",
    "呲牙": "呲牙", "牙": "呲牙",
    "惊讶": "惊讶", "惊": "惊讶",
    "难过": "难过", "伤心": "难过",
    "冷汗": "冷汗",
    "抓狂": "抓狂", "崩溃": "抓狂",
    "吐": "吐",
    "偷笑": "偷笑",
    "可爱": "可爱", "萌": "可爱",
    "白眼": "白眼",
    "傲慢": "傲慢", "骄傲": "傲慢",
    "饥饿": "饥饿", "饿": "饥饿",
    "困": "困", "犯困": "困",
    "惊恐": "惊恐", "恐惧": "惊恐",
    "流汗": "流汗", "汗": "流汗",
    "憨笑": "憨笑", "傻笑": "憨笑",
    "悠闲": "悠闲", "惬意": "悠闲",
    "奋斗": "奋斗", "加油": "奋斗",
    "咒骂": "咒骂",
    "疑问": "疑问", "疑惑": "疑问",
    "晕": "晕", "头晕": "晕",
    "折磨": "折磨",
    "衰": "衰", "倒霉": "衰",
    "骷髅": "骷髅",
    "敲打": "敲打", "锤": "敲打",
    "再见": "再见", "拜拜": "再见",
    "擦汗": "擦汗",
    "抠鼻": "抠鼻", "抠鼻子": "抠鼻",
    "鼓掌": "鼓掌", "拍手": "鼓掌",
    "糗大了": "糗大了",
    "坏笑": "坏笑", "邪笑": "坏笑",
    "左哼哼": "左哼哼", "右哼哼": "右哼哼", "哼": "右哼哼",
    "哈欠": "哈欠", "打哈欠": "哈欠",
    "鄙视": "鄙视",
    "委屈": "委屈",
    "快哭了": "快哭了",
    "阴险": "阴险",
    "亲亲": "亲亲", "么么": "亲亲",
    "吓": "吓", "吓到": "吓",
    "可怜": "可怜",
    "菜刀": "菜刀", "刀": "菜刀",
    "西瓜": "西瓜",
    "啤酒": "啤酒",
    "咖啡": "咖啡",
    "饭": "饭", "吃饭": "饭",
    "猪头": "猪头",
    "玫瑰": "玫瑰", "花": "玫瑰",
    "凋谢": "凋谢",
    "示爱": "示爱",
    "爱心": "爱心", "心": "爱心",
    "拥抱": "拥抱", "抱抱": "拥抱",
    "强": "强", "赞": "强", "厉害": "强", "棒": "强", "牛": "强",
    "弱": "弱", "菜": "弱",
    "握手": "握手",
    "胜利": "胜利", "耶": "胜利",
    "抱拳": "抱拳",
    "勾引": "勾引",
    "拳头": "拳头", "拳": "拳头",
    "差劲": "差劲",
    "爱你": "爱你",
    "NO": "NO", "不": "NO",
    "OK": "OK", "好": "OK",
    "转圈": "转圈",
    "磕头": "磕头",
    "回头": "回头",
    "跳绳": "跳绳",
    "挥手": "挥手",
    "激动": "激动",
    "街舞": "街舞",
    "献吻": "献吻",
    "左太极": "左太极", "右太极": "右太极",
    "doge": "doge",
    "捂脸": "捂脸", "捂脸哭": "捂脸",
    "笑哭": "笑哭", "哭笑": "笑哭",
    "嘿哈": "嘿哈",
    "捂嘴笑": "捂嘴笑",
    "思考": "思考", "想想": "思考",
    "泪奔": "泪奔",
}


def _normalize_qq_faces(text: str) -> str:
    """将 AI 回复中的表情文本规范化为 NTQQ 可识别的格式。
    处理 [微笑]、【微笑】、（微笑）等变体，统一为 [标准名]。
    仅做精确别名匹配，避免误改正常文本。
    """
    def _replace_face(m):
        raw = m.group(1).strip()
        if raw in _QQ_FACE_ALIASES:
            return f"[{_QQ_FACE_ALIASES[raw]}]"
        return m.group(0)  # 不认识就原样保留

    # 匹配 【】、（） 包裹的疑似表情 → 转为 [] 格式
    text = re.sub(r'[【（]\s*([^】）]{1,6}?)\s*[】）]', _replace_face, text)
    # 处理 [] 包裹但别名不同的情况（如 [笑脸] → [微笑]）
    text = re.sub(r'\[\s*([^[\]]{1,6}?)\s*\]', _replace_face, text)
    return text


# QQ 表情名 → face ID 映射（NapCat QSid 标准名）
# 只包含 _QQ_FACE_ALIASES 中使用的标准名
_QQ_FACE_ID_MAP: dict[str, int] = {
    "惊讶": 0, "撇嘴": 1, "色": 2, "发呆": 3, "得意": 4, "流泪": 5,
    "害羞": 6, "闭嘴": 7, "睡": 8, "大哭": 9, "尴尬": 10, "发怒": 11,
    "调皮": 12, "呲牙": 13, "微笑": 14, "难过": 15, "酷": 16,
    "抓狂": 18, "吐": 19, "偷笑": 20, "可爱": 21, "白眼": 22, "傲慢": 23,
    "饥饿": 24, "困": 25, "惊恐": 26, "流汗": 27, "憨笑": 28, "悠闲": 29,
    "奋斗": 30, "咒骂": 31, "疑问": 32, "嘘": 33, "晕": 34, "折磨": 35,
    "衰": 36, "骷髅": 37, "敲打": 38, "再见": 39,
    "猪头": 46, "拥抱": 49, "蛋糕": 53, "闪电": 54, "炸弹": 55,
    "刀": 56, "足球": 57, "便便": 59, "咖啡": 60, "饭": 61,
    "玫瑰": 63, "凋谢": 64, "爱心": 66, "心碎": 67, "礼物": 69,
    "太阳": 74, "月亮": 75,
    "握手": 78, "胜利": 79, "飞吻": 85, "西瓜": 89,
    "冷汗": 96, "擦汗": 97, "抠鼻": 98, "鼓掌": 99,
    "糗大了": 100, "坏笑": 101, "左哼哼": 102, "右哼哼": 103,
    "哈欠": 104, "鄙视": 105, "委屈": 106, "快哭了": 107,
    "阴险": 108, "吓": 110, "可怜": 111, "菜刀": 112,
    "啤酒": 113, "篮球": 114, "乒乓": 115, "示爱": 116,
    "抱拳": 118, "勾引": 119, "拳头": 120, "差劲": 121,
    "爱你": 122, "NO": 123, "OK": 124, "转圈": 125,
    "磕头": 126, "回头": 127, "跳绳": 128, "挥手": 129,
    "激动": 130, "街舞": 131, "献吻": 132, "左太极": 133, "右太极": 134,
    "泪奔": 173, "doge": 179, "笑哭": 182, "大笑": 193,
    "捂脸": 264, "吃瓜": 271, "加油": 315,
    # NapCat 标准名与 _QQ_FACE_ALIASES 不匹配的补充：
    "强": 76, "弱": 77, "亲亲": 109, "嘿哈": 264, "思考": 269,
    "捂嘴笑": 183, "饭": 61, "菜刀": 112, "西瓜": 89, "啤酒": 113,
    "咖啡": 60, "猪头": 46, "玫瑰": 63, "凋谢": 64, "示爱": 116,
    "爱心": 66, "拥抱": 49, "胜利": 79, "勾引": 119, "差劲": 121,
}


def _parse_qq_faces(text: str) -> Message:
    """将文本中的 [表情名] 解析为 QQ 原生表情段 + 文本段的混合 Message。
    先调用 _normalize_qq_faces 规范化别名，再查找 _QQ_FACE_ID_MAP 获取 face ID。
    未识别的 [表情名] 保留为文本（由 QQ 客户端尝试本地渲染）。
    """
    text = _normalize_qq_faces(text)
    msg = Message()
    pattern = re.compile(r'\[([^\[\]]{1,8})\]')
    last_end = 0
    for m in pattern.finditer(text):
        # 添加匹配前的纯文本
        before = text[last_end:m.start()]
        if before:
            msg += MessageSegment.text(before)
        face_name = m.group(1)
        face_id = _QQ_FACE_ID_MAP.get(face_name)
        if face_id is not None:
            msg += MessageSegment.face(face_id)
        else:
            # 未映射的表情名保留原样（QQ 客户端可能本地识别）
            msg += MessageSegment.text(m.group(0))
        last_end = m.end()
    # 添加尾部文本
    remaining = text[last_end:]
    if remaining:
        msg += MessageSegment.text(remaining)
    return msg if msg else Message(text)


# ============================================================
# 消息规则
# ============================================================

def _is_at_me(event: GroupMessageEvent, bot: Bot) -> bool:
    # 优先使用 NoneBot2 内置的 to_me 属性
    if getattr(event, 'to_me', False):
        return True
    # 兜底：手动检查 at 段
    self_id = str(bot.self_id)
    for seg in event.message:
        if seg.type == "at" and str(seg.data.get("qq", "")) == self_id:
            return True
    return False


def _is_command(text: str) -> bool:
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


async def _respond_rule(event: Event) -> bool:
    logger.info(f"[RULE] Event type={type(event).__name__}, user={event.get_user_id()}")
    if isinstance(event, PrivateMessageEvent):
        return True
    if isinstance(event, GroupMessageEvent):
        try:
            bot = nonebot.get_bot()
            result = _is_at_me(event, bot)
            if not result:
                logger.debug(
                    f"Group msg ignored: to_me={getattr(event, 'to_me', None)}, "
                    f"self_id={bot.self_id}, segments={[(s.type, s.data) for s in event.message]}"
                )
            return result
        except Exception as e:
            logger.error(f"_respond_rule error: {e}")
            return False
    return False


# ============================================================

# 生命周期
# ============================================================

_cleanup_task: Optional[asyncio.Task] = None


def _safe_create_task(coro, name: str = ""):
    """创建带错误日志的后台任务"""
    task = asyncio.create_task(coro, name=name)
    def _on_done(t):
        if not t.cancelled() and t.exception():
            logger.error(f"Background task '{name}' failed: {t.exception()}")
    task.add_done_callback(_on_done)
    return task


@driver.on_startup
async def on_startup():
    global _cleanup_task
    logger.info(f"zyw QQ Bot 启动成功")
    logger.info(f"已加载 {len(ALL_SKILLS)} 个角色 Skill")
    if DEFAULT_SKILL_NAME:
        logger.info(f"默认角色：{DEFAULT_SKILL_NAME}")
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY.startswith("sk-your"):
        logger.warning("DEEPSEEK_API_KEY 未配置！请在 .env 文件中设置。")
    # 显示 LLM Provider 配置
    if OPENAI_ENABLED and OPENAI_API_KEY and OPENAI_BASE_URL:
        logger.info(f"LLM Provider: OpenAI (primary) | model={OPENAI_MODEL} | base={OPENAI_BASE_URL}")
        logger.info(f"LLM Provider: DeepSeek (fallback) | model={DEEPSEEK_MODEL}")
    else:
        logger.info(f"LLM Provider: DeepSeek (only) | model={DEEPSEEK_MODEL}")
    # 初始化持久化 HTTP 客户端
    _get_http_client()
    # 加载情绪表情文件
    _load_emoji_files()
    # 启动定期对话历史清理
    _cleanup_task = _safe_create_task(_periodic_history_cleanup(), "history-cleanup")
    logger.info("HTTP client initialized, history cleanup task started")


@driver.on_shutdown
async def on_shutdown():
    global _http_client, _cleanup_task
    # 关闭 HTTP 客户端
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None
    # 取消清理任务
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
    logger.info("zyw QQ Bot 已关闭（HTTP client closed, cleanup task cancelled）")
