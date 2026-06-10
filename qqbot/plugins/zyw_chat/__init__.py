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
# 配置
# ============================================================

driver = get_driver()
config = driver.config

DEEPSEEK_API_KEY: str = getattr(config, "deepseek_api_key", "")
DEEPSEEK_BASE_URL: str = getattr(config, "deepseek_base_url", "https://api.deepseek.com")
DEEPSEEK_MODEL: str = getattr(config, "deepseek_model", "deepseek-chat")
DEEPSEEK_SEARCH_MODEL: str = "deepseek-v4-flash"  # 联网搜索时使用更快的模型

# OpenAI API（主选模型，失败时回退 DeepSeek）
_openai_enabled_raw = getattr(config, "openai_enabled", "false")
OPENAI_ENABLED: bool = str(_openai_enabled_raw).lower() == "true"
OPENAI_API_KEY: str = getattr(config, "openai_api_key", "")
OPENAI_BASE_URL: str = getattr(config, "openai_base_url", "")
OPENAI_MODEL: str = getattr(config, "openai_model", "gpt-4o")

# Provider 健康状态（primary=openai, fallback=deepseek）
_provider_health: dict[str, dict] = {
    "openai": {"healthy": True, "fail_count": 0, "last_fail": 0},
    "deepseek": {"healthy": True, "fail_count": 0, "last_fail": 0},
}
_PROVIDER_COOLDOWN = 120  # 标记不健康后的冷却秒数


def _get_provider(name: str) -> dict:
    """获取指定 provider 的配置信息"""
    if name == "openai":
        return {"name": "openai", "base_url": OPENAI_BASE_URL, "api_key": OPENAI_API_KEY, "model": OPENAI_MODEL}
    return {"name": "deepseek", "base_url": DEEPSEEK_BASE_URL, "api_key": DEEPSEEK_API_KEY, "model": DEEPSEEK_MODEL}


def _get_active_provider() -> dict:
    """返回当前可用的主 provider（优先 OpenAI，回退 DeepSeek）"""
    now = time.time()
    if OPENAI_ENABLED and OPENAI_API_KEY and OPENAI_BASE_URL:
        h = _provider_health["openai"]
        if h["healthy"] or (now - h["last_fail"] > _PROVIDER_COOLDOWN):
            h["healthy"] = True
            return _get_provider("openai")
    return _get_provider("deepseek")


def _mark_provider_failed(name: str):
    """标记 provider 失败，连续失败则进入冷却"""
    h = _provider_health.get(name)
    if not h:
        return
    h["fail_count"] += 1
    h["last_fail"] = time.time()
    if h["fail_count"] >= 2:
        h["healthy"] = False
        logger.warning(f"Provider '{name}' 连续失败 {h['fail_count']} 次，进入 {_PROVIDER_COOLDOWN}s 冷却")


def _mark_provider_healthy(name: str):
    """标记 provider 恢复健康"""
    h = _provider_health.get(name)
    if h:
        h["healthy"] = True
        h["fail_count"] = 0


def _adapt_payload_for_provider(payload: dict, provider_name: str) -> dict:
    """根据 provider 调整 payload 参数（OpenAI 不支持某些 DeepSeek 特有参数）"""
    p = {**payload}
    if provider_name == "openai":
        # OpenAI 不支持 reasoning_effort、frequency_penalty 等部分参数
        p.pop("reasoning_effort", None)
        p.pop("frequency_penalty", None)
        # tool_choice 处理：如果值为 "auto"，保持不变；否则移除
        if "tool_choice" in p and p["tool_choice"] not in ("auto", "none", "required"):
            p.pop("tool_choice", None)
    return p

# 活跃时间开关
# ACTIVE_HOURS=0,23 表示全天活跃；ACTIVE_HOURS=9,22 表示 9:00-22:00 活跃
ACTIVE_HOURS_START: int = int(getattr(config, "active_hours_start", 0))
ACTIVE_HOURS_END: int = int(getattr(config, "active_hours_end", 23))

# Web Search 功能
_web_search_raw = getattr(config, "web_search_enabled", "true")
WEB_SEARCH_ENABLED: bool = str(_web_search_raw).lower() == "true" if _web_search_raw is not None else True

# 搜狗搜索（替代 DuckDuckGo/百度，国内直连无需第三方库）
_SEARCH_AVAILABLE = True

# ── 搜索健康检查 ──
_search_health = {
    "sogou": {"fail_count": 0, "last_fail": 0, "healthy": True},
    "baidu": {"fail_count": 0, "last_fail": 0, "healthy": True},
    "weibo": {"fail_count": 0, "last_fail": 0, "healthy": True},
}
_SEARCH_HEALTH_FAIL_THRESHOLD = 3    # 连续失败次数达到此值标记为不健康（从2改为3，减少误判）
_SEARCH_HEALTH_COOLDOWN = 120         # 不健康后等待秒数再重试（从300改为120，更快恢复）


def _is_search_healthy(source: str) -> bool:
    """检查搜索源是否健康可用"""
    h = _search_health.get(source)
    if not h:
        return True
    if h["healthy"]:
        return True
    if time.time() - h["last_fail"] > _SEARCH_HEALTH_COOLDOWN:
        # 冷却期已过，标记为健康并重试
        h["healthy"] = True
        h["fail_count"] = 0
        logger.info(f"[search_health] {source} 冷却期已过，重新启用")
        return True
    return False


def _update_search_health(source: str, success: bool):
    """更新搜索源健康状态"""
    h = _search_health.get(source)
    if not h:
        return
    if success:
        if not h["healthy"]:
            logger.info(f"[search_health] {source} 恢复正常")
        h["fail_count"] = 0
        h["healthy"] = True
    else:
        h["fail_count"] += 1
        h["last_fail"] = time.time()
        if h["fail_count"] >= _SEARCH_HEALTH_FAIL_THRESHOLD:
            h["healthy"] = False
            logger.warning(
                f"[search_health] {source} 连续失败 {h['fail_count']} 次，"
                f"暂停 {_SEARCH_HEALTH_COOLDOWN}s"
            )


# ── 搜索意图关键词（命中时跳过 probe，强制走工具调用路径） ──
_SEARCH_INTENT_KEYWORDS = [
    "查一下", "搜一下", "帮我查", "帮我搜", "搜索", "查找", "查查",
    "了解一下", "找找", "看看", "是什么", "是谁", "谁啊", "哪个团",
    "什么团", "哪里的", "介绍一下", "介绍下", "科普", "百科", "资料",
    "wiki", "微博", "百度", "知乎", "fandom", "官网",
    "你知道吗", "你知道", "听说过", "认识吗", "了解吗",
    "最近", "最新", "什么时候", "在哪里",
    "有哪些", "都有谁", "都有啥", "都有什么", "都有哪",
    "什么成员", "几个成员", "哪些成员", "成员有", "名单", "一览",
    "什么来头", "干嘛的", "做什么的", "干什么的", "什么来路",
    "怎么回事", "发生过什么", "有啥", "有谁",
    # 追问 / 跨人 / 跨话题
    "她呢", "他呢", "那她", "那他", "那小", "那大",
    "其他人", "别人呢", "队友呢", "队员呢",
    "几月", "几号", "多大", "多高", "哪里人",
    "关于", "说说", "讲讲",
]
_URL_PATTERN = re.compile(r'https?://\S+')


def _has_search_intent(text: str) -> bool:
    """检测用户消息是否包含搜索意图（命中则跳过 probe 直接走工具调用）"""
    lower = text.lower()
    for kw in _SEARCH_INTENT_KEYWORDS:
        if kw in lower:
            return True
    if _URL_PATTERN.search(text):
        return True
    return False


MAX_HISTORY_ROUNDS = 15
REQUEST_TIMEOUT = 60
MAX_HISTORY_MESSAGES = MAX_HISTORY_ROUNDS * 2
ADMIN_QQ = getattr(config, "admin_qq", "")
DEFAULT_SKILL_NAME_CONFIG: str = getattr(config, "default_skill", "")  # .env 中配置的默认角色

PLUGIN_DIR = Path(__file__).parent
QQBOT_DIR = PLUGIN_DIR.parent.parent
SKILLS_DIR = QQBOT_DIR / "skills"
PHOTO_DIR = QQBOT_DIR.parent / "photo"



# ── 运行时参数配置（可通过 Dashboard 动态调整） ──
_RUNTIME_CONFIG_FILE = QQBOT_DIR / "data" / "runtime_settings.json"
_SETTINGS_RELOAD_TRIGGER = QQBOT_DIR / ".reload_settings_trigger"

_RUNTIME_CONFIG_DEFAULTS = {
    "active_hours_start": 0,
    "active_hours_end": 23,
    "web_search_enabled": True,
    "stream_enabled": True,
    "stream_flush_chars": 60,
    "stream_flush_interval": 8.0,
    "stream_flush_min_chars": 80,
    "stream_max_flush_size": 300,
    "max_history_rounds": 15,
    "history_ttl_hours": 6,
    "history_save_interval": 60,
    "thinking_timer_seconds": 5,
    "multi_turn_enabled": True,
}

_runtime_config: dict = {}


def _load_runtime_config():
    """从磁盘加载运行时配置，缺失项用默认值填充"""
    global _runtime_config
    _runtime_config = dict(_RUNTIME_CONFIG_DEFAULTS)
    if _RUNTIME_CONFIG_FILE.exists():
        try:
            saved = json.loads(_RUNTIME_CONFIG_FILE.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k in _RUNTIME_CONFIG_DEFAULTS:
                    expected = type(_RUNTIME_CONFIG_DEFAULTS[k])
                    _runtime_config[k] = expected(v)
        except Exception as e:
            logger.warning(f"[settings] Failed to load runtime config: {e}")


def _save_runtime_config():
    """持久化运行时配置到磁盘"""
    try:
        _RUNTIME_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _RUNTIME_CONFIG_FILE.write_text(
            json.dumps(_runtime_config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"[settings] Failed to save runtime config: {e}")


def _apply_runtime_config():
    """将运行时配置同步到模块级变量（供现有代码路径直接读取）"""
    global ACTIVE_HOURS_START, ACTIVE_HOURS_END, WEB_SEARCH_ENABLED
    global _STREAM_ENABLED, _STREAM_FLUSH_CHARS, _STREAM_FLUSH_INTERVAL, _STREAM_FLUSH_MIN_CHARS, _STREAM_SOFT_BREAKS, _STREAM_MAX_FLUSH_SIZE
    global MAX_HISTORY_ROUNDS, MAX_HISTORY_MESSAGES
    global _HISTORY_TTL, _HISTORY_SAVE_INTERVAL

    ACTIVE_HOURS_START = int(_runtime_config.get("active_hours_start", 0))
    ACTIVE_HOURS_END = int(_runtime_config.get("active_hours_end", 23))
    WEB_SEARCH_ENABLED = bool(_runtime_config.get("web_search_enabled", True))
    _STREAM_ENABLED = bool(_runtime_config.get("stream_enabled", True))
    _STREAM_FLUSH_CHARS = int(_runtime_config.get("stream_flush_chars", 60))
    _STREAM_FLUSH_INTERVAL = float(_runtime_config.get("stream_flush_interval", 8.0))
    _STREAM_FLUSH_MIN_CHARS = int(_runtime_config.get("stream_flush_min_chars", 80))
    _STREAM_MAX_FLUSH_SIZE = int(_runtime_config.get("stream_max_flush_size", 300))
    MAX_HISTORY_ROUNDS = int(_runtime_config.get("max_history_rounds", 15))
    MAX_HISTORY_MESSAGES = MAX_HISTORY_ROUNDS * 2
    _HISTORY_TTL = int(_runtime_config.get("history_ttl_hours", 6)) * 3600
    _HISTORY_SAVE_INTERVAL = int(_runtime_config.get("history_save_interval", 60))


_load_runtime_config()
_apply_runtime_config()

# ── 聊天事件专用日志（写入 chat.log，方便排查联网/对话问题）──
_LOG_DIR = QQBOT_DIR / "logs"
_LOG_DIR.mkdir(exist_ok=True)
chat_logger = logging.getLogger("zyw_chat.events")
chat_logger.setLevel(logging.INFO)
_fh = logging.FileHandler(_LOG_DIR / "chat.log", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%m-%d %H:%M:%S"))
chat_logger.addHandler(_fh)
chat_logger.propagate = False

# ============================================================
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
# DeepSeek API — 持久化客户端 + 重试
# ============================================================

_http_client: Optional[httpx.AsyncClient] = None
_API_MAX_RETRIES = 3
_API_RETRY_BASE_DELAY = 2  # 秒

# ── 流式输出 ──
_STREAM_ENABLED = str(getattr(config, "deepseek_stream", "true")).lower() == "true"
_STREAM_FLUSH_CHARS = 60      # 累积多少字符后寻找句末断点
_STREAM_FLUSH_INTERVAL = 8.0  # 最长等待秒数（即使没到句末也发送，但需满足最低字符数+软断点）
_STREAM_FLUSH_MIN_CHARS = 80  # 时间触发刷新的最低字符数（避免太短片段被超时发出）
_STREAM_SOFT_BREAKS = set("，,；;：:、\n ")  # 软断点字符（time_flush 在最近20字内需命中其中一个）
_STREAM_MAX_FLUSH_SIZE = 300  # 单段最大字符数（强制断句）

# ── 并发控制 ──
_API_SEMAPHORE = asyncio.Semaphore(30)       # 全局最多 30 个 API 请求同时进行
_user_processing: dict[str, asyncio.Lock] = {}  # 每个用户一把锁，防止同一用户连发消息重复调 API


def _get_user_lock(uid: str) -> asyncio.Lock:
    """获取/创建用户级别的锁（惰性初始化）"""
    if uid not in _user_processing:
        _user_processing[uid] = asyncio.Lock()
    return _user_processing[uid]


# ── 情绪表情系统 ──

_EMOJI_DIR = Path(os.environ.get("EMOJI_DIR", r"D:\agent_function\skill_communication\emoji"))
_EMOJI_PROBABILITY = 0.50  # 50% 概率发送表情
_EMOJI_COOLDOWN = {}       # per-user cooldown to avoid spamming
_EMOJI_IMG_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}

# 内置默认关键词（当文件夹没有 keywords.txt 时使用）
_EMOJI_DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "angry": [
        "生气", "气死", "烦死", "讨厌", "无语", "受不了", "怒", "滚", "去死",
        "混蛋", "靠", "恼火", "火大", "发火", "暴躁", "气炸", "不爽", "烦人",
        "找打", "想打人", "想锤", "想揍", "拳头", "拳头硬了",
    ],
    "sad": [
        "难过", "伤心", "呜呜", "哭", "委屈", "可怜", "心疼", "遗憾", "可惜",
        "唉", "惨", "悲伤", "郁闷", "失落", "泪", "心酸", "emo", "破防",
        "想哭", "哭了", "好惨", "太惨", "悲惨",
    ],
    "happy": [
        "开心", "高兴", "太好了", "哈哈", "嘻嘻", "嘿嘿", "好耶", "耶",
        "喜欢", "爱了", "甜", "暖", "完美", "厉害", "太棒了", "赞",
        "幸福", "快乐", "好喜欢", "超爱", "可爱", "贴贴", "mua",
    ],
    "joker": [
        "笑死", "离谱", "绝了", "抽象", "牛", "666", "乐子", "整活",
        "乐", "哈哈哈哈哈", "笑喷", "绷不住", "搞笑", "太搞笑了", "草",
        "逆天", "人才", "鬼才", "秀", "整挺好", "会整活",
    ],
}

# 运行时动态数据（由 _load_emoji_files 填充）
_EMOJI_FILES: dict[str, list[Path]] = {}       # emotion_name -> [image_paths]
_EMOJI_EMOTIONS: dict[str, list[str]] = {}     # emotion_name -> [keywords]


def _load_emoji_files():
    """扫描表情目录，自动发现所有子文件夹并加载图片和关键词。
    
    每个子文件夹：
    - 文件夹名 = 情绪类别
    - keywords.txt = 关键词文件（每行一个关键词，# 开头为注释）
    - 图片文件 = jpg/jpeg/png/gif/webp
    """
    global _EMOJI_FILES, _EMOJI_EMOTIONS
    _EMOJI_FILES = {}
    _EMOJI_EMOTIONS = {}

    if not _EMOJI_DIR.exists():
        logger.warning(f"[EMOJI] 表情目录不存在: {_EMOJI_DIR}")
        return

    for folder in sorted(_EMOJI_DIR.iterdir()):
        if not folder.is_dir():
            continue
        emotion = folder.name

        # 加载图片
        imgs = [f for f in folder.iterdir()
                if f.is_file() and f.suffix.lower() in _EMOJI_IMG_EXTS]
        if not imgs:
            continue
        _EMOJI_FILES[emotion] = imgs

        # 加载关键词：优先 keywords.txt，否则用内置默认
        kw_file = folder / "keywords.txt"
        if kw_file.exists():
            try:
                keywords = []
                for line in kw_file.read_text(encoding='utf-8').splitlines():
                    line = line.strip()
                    if line and not line.startswith('#'):
                        keywords.append(line)
                if keywords:
                    _EMOJI_EMOTIONS[emotion] = keywords
                    logger.info(f"[EMOJI] {emotion}: {len(imgs)} 图片, {len(keywords)} 关键词 (from keywords.txt)")
                else:
                    _EMOJI_EMOTIONS[emotion] = _EMOJI_DEFAULT_KEYWORDS.get(emotion, [])
                    logger.info(f"[EMOJI] {emotion}: {len(imgs)} 图片, keywords.txt 为空，使用默认关键词")
            except Exception as e:
                logger.warning(f"[EMOJI] 读取 {emotion}/keywords.txt 失败: {e}")
                _EMOJI_EMOTIONS[emotion] = _EMOJI_DEFAULT_KEYWORDS.get(emotion, [])
        else:
            _EMOJI_EMOTIONS[emotion] = _EMOJI_DEFAULT_KEYWORDS.get(emotion, [])
            logger.info(f"[EMOJI] {emotion}: {len(imgs)} 图片, 使用默认关键词")

    total_imgs = sum(len(v) for v in _EMOJI_FILES.values())
    emotions = list(_EMOJI_FILES.keys())
    logger.info(f"[EMOJI] 加载完成: {total_imgs} 个表情, {len(emotions)} 种情绪: {emotions}")


def _detect_emotion(user_text: str, bot_reply: str) -> str | None:
    """根据用户消息和 bot 回复检测情绪，多个命中时随机选一个。"""
    import random as _rand
    combined = (user_text + " " + bot_reply).lower()
    matched = []
    for emotion, keywords in _EMOJI_EMOTIONS.items():
        for kw in keywords:
            if kw.lower() in combined:
                matched.append(emotion)
                break  # 同一情绪只计一次
    return _rand.choice(matched) if matched else None


async def _maybe_send_emoji(user_text: str, bot_reply: str, uid: str):
    """根据对话情绪概率发送表情图片。"""
    import random

    # 冷却检查（同一用户 60 秒内最多发一次表情）
    now = time.time()
    last_sent = _EMOJI_COOLDOWN.get(uid, 0)
    if now - last_sent < 60:
        return

    # 概率判断
    if random.random() > _EMOJI_PROBABILITY:
        return

    # 情绪检测
    emotion = _detect_emotion(user_text, bot_reply)
    if not emotion:
        return

    # 获取对应情绪的表情文件
    files = _EMOJI_FILES.get(emotion, [])
    if not files:
        return

    # 随机选一张
    img_path = random.choice(files)
    try:
        # GIF 用 base64 发送以保证动画效果，其他格式用 file URI
        if img_path.suffix.lower() == '.gif':
            img_bytes = img_path.read_bytes()
            b64 = base64.b64encode(img_bytes).decode('ascii')
            await zyw_chat.send(MessageSegment.image(file=f"base64://{b64}"))
        else:
            file_uri = f"file:///{img_path.as_posix()}"
            await zyw_chat.send(MessageSegment.image(file=file_uri))
        _EMOJI_COOLDOWN[uid] = now
        chat_logger.info(f"[EMOJI] 发送表情: emotion={emotion}, file={img_path.name}, user={uid}")
    except Exception as e:
        logger.warning(f"[EMOJI] 发送失败: {e}")

# ── Function Calling 工具定义 ──
_SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "在网络上搜索实时信息。系统会从百度、微博、DuckDuckGo 等多个来源并行搜索，"
                "并自动抓取百科类页面的详细内容。"
                "当用户询问偶像、艺人、演出活动、最新新闻等话题时应主动使用。"
                "遇到不确定的话题应主动搜索，宁可多搜也不要编造。"
                "搜索结果包含可点击的链接，可以提供给用户参考。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "搜索关键词。要求：1.尽量简短，只写核心名称（如'阵雨电台'而非'阵雨电台 地下偶像'）"
                            "2.不要加修饰词（如'官方''是谁''介绍'等）3.微博搜索对短关键词效果更好，"
                            "关键词越长越容易搜不到结果"
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回结果数量，默认 8。需要详细信息时建议设为 10",
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_corpus",
            "description": (
                "搜索角色的语料库/知识库（包括角色设定、背景故事、工作经历等所有资料文件）。"
                "当用户询问与角色自身相关的问题时使用此工具，确保回答与角色设定一致。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "string",
                        "description": "搜索关键词，可以是空格分隔的多个关键词",
                    },
                },
                "required": ["keywords"],
            },
        },
    },
]
_MAX_TOOL_ROUNDS = 3  # 增加到 3 轮，支持复杂查询的多步搜索


# ── 联网搜索优先站点（搜狗 site: 搜索） ──
# 注：chinaidols.fandom.com 被 Cloudflare 403 封锁，cmks.top SSL 证书失效，均已移除
_PRIORITY_SITES = [
    "weibo.com",
    "baike.baidu.com",
]

# ── 微博直搜（使用 cookie 调微博 API，不走 DuckDuckGo） ──
_WEIBO_COOKIES_FILE = Path(r"D:\agent_function\skill_communication\cookies.json")
_weibo_cookies_cache: Optional[dict] = None  # {"cookie_str": ..., "xsrf": ...}
_weibo_cookies_mtime: float = 0


def _load_weibo_cookies() -> Optional[dict]:
    """加载微博 cookie，返回 {"cookie_str": ..., "xsrf": ...}，带文件变更缓存"""
    global _weibo_cookies_cache, _weibo_cookies_mtime
    if not _WEIBO_COOKIES_FILE.exists():
        return None
    try:
        mtime = _WEIBO_COOKIES_FILE.stat().st_mtime
        if _weibo_cookies_cache and mtime == _weibo_cookies_mtime:
            return _weibo_cookies_cache
        with open(_WEIBO_COOKIES_FILE, "r", encoding="utf-8") as f:
            cookies_list = json.load(f)
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies_list)
        xsrf = ""
        for c in cookies_list:
            if c["name"] == "XSRF-TOKEN":
                xsrf = c["value"]
                break
        _weibo_cookies_cache = {"cookie_str": cookie_str, "xsrf": xsrf}
        _weibo_cookies_mtime = mtime
        return _weibo_cookies_cache
    except Exception as e:
        logger.warning(f"[weibo] 加载 cookies.json 失败: {e}")
        return None


async def _search_weibo_direct(query: str, max_results: int = 5, timeout_s: float = 30) -> list:
    """用 cookie 调微博全局搜索 API (statuses/search)，返回统一格式的结果列表"""
    cookie_data = _load_weibo_cookies()
    if not cookie_data:
        logger.warning("[weibo] cookies.json 不存在或加载失败，跳过微博直搜")
        return []

    from urllib.parse import urlencode
    params = {"q": query, "count": max_results, "page": 1}
    url = f"https://weibo.com/ajax/statuses/search?{urlencode(params)}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Cookie": cookie_data["cookie_str"],
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://weibo.com/",
    }
    if cookie_data.get("xsrf"):
        headers["X-XSRF-TOKEN"] = cookie_data["xsrf"]

    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code in (301, 302, 303):
            logger.warning(f"[weibo] cookie 已过期 (HTTP {resp.status_code})，需要更新 cookies.json")
            return []

        resp.raise_for_status()
        data = resp.json()

        # 检测 cookie 过期：ok=-100 表示未登录
        if data.get("ok") == -100:
            logger.warning("[weibo] cookie 已过期 (ok=-100)，需要更新 cookies.json")
            return []

        if data.get("ok") != 1:
            raw = json.dumps(data, ensure_ascii=False)[:300]
            logger.warning(f"[weibo] API 返回异常: {raw}")
            return []

        results = []
        items = data.get("statuses", [])
        total = data.get("total_number", 0)
        for item in items:
            if len(results) >= max_results:
                break
            _extract_weibo_item(item, results, max_results)

        logger.info(f"[weibo] 直搜完成: query='{query[:40]}' total={total} items={len(items)} results={len(results)}")
        return results

    except httpx.TimeoutException:
        logger.warning(f"[weibo] 直搜超时 ({timeout_s}s): {query[:60]}")
        return []
    except Exception as e:
        logger.warning(f"[weibo] 直搜失败: {e}")
        return []


def _extract_weibo_item(item: dict, results: list, max_results: int):
    """从微博桌面端搜索结果 item 提取搜索条目"""
    if len(results) >= max_results:
        return
    # 桌面端 API 提供 text_raw（纯文本）和 text（含 HTML），优先用 text_raw
    text = item.get("text_raw", "")
    if not text:
        import re as _re
        text = _re.sub(r'<[^>]+>', '', item.get("text", "")).strip()
    user_info = item.get("user", {})
    username = user_info.get("screen_name", "未知用户")
    mid = item.get("mid") or item.get("id", "")
    link = f"https://weibo.com/{user_info.get('id', '')}/{mid}" if mid and user_info.get("id") else ""
    snippet = text[:200] + ("..." if len(text) > 200 else "")
    results.append({
        "title": f"@{username}: {text[:50]}...",
        "href": link,
        "url": link,
        "body": snippet,
    })


# ── 微博用户搜索（已废弃，endpoint 返回 404）──
async def _search_weibo_user(query: str, max_results: int = 3, timeout_s: float = 20) -> list:
    """搜索微博用户 — endpoint /ajax/side/cards/searchUser 已下线，始终返回空"""
    logger.debug(f"[weibo] 用户搜索已废弃，跳过: {query[:40]}")
    return []


# ── 关键词简化 ──
_GENERIC_SUFFIXES = {"官方", "是谁", "介绍", "资料", "简介", "哪里人", "怎么样", "什么"}


def _extract_core_keywords(query: str) -> str:
    """从搜索查询中提取核心关键词，用于重试简化。
    '阵雨电台 地下偶像' → '阵雨电台'
    'XXX 是谁' → 'XXX'
    """
    parts = query.strip().split()
    if len(parts) <= 1:
        return query

    # 去除空格分隔的通用修饰词
    filtered = [p for p in parts if p not in _GENERIC_SUFFIXES]
    if not filtered:
        return parts[0]

    # 返回第一个核心词（通常是主体名称）
    return filtered[0]


async def _search_sogou(query: str, max_results: int = 10, timeout_s: float = 12) -> list:
    """搜索搜狗网页，解析 HTML 返回结构化结果列表"""
    from urllib.parse import quote_plus

    url = f"https://www.sogou.com/web?query={quote_plus(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text
    except httpx.TimeoutException:
        logger.warning(f"[sogou] 搜索超时: {query[:60]}")
        return []
    except Exception as e:
        logger.warning(f"[sogou] 搜索失败: {e}")
        return []

    # 轻量级 HTML 解析
    _TAG_RE = re.compile(r'<[^>]+>')
    _ENTITY_RE = re.compile(r'&(?:nbsp|amp|lt|gt|quot|#\d+|#x[\da-fA-F]+);')

    def _clean(text: str) -> str:
        text = _TAG_RE.sub('', text)
        text = _ENTITY_RE.sub(' ', text)
        return ' '.join(text.split())

    results = []
    # 搜狗搜索结果以 class 含 vrwrap 或 rb 的容器标记
    containers = re.split(r'<div[^>]*class="[^"]*(?:vrwrap|rb)[^"]*"', html)
    for block in containers[1:]:  # 第一段是页面头部，跳过
        # 提取标题（<h3> 标签内容）
        h3 = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
        if not h3:
            continue
        title = _clean(h3.group(1))
        if not title:
            continue
        # 提取链接（h3 内的 <a> href）
        link = ""
        href_match = re.search(r'<a[^>]*href="([^"]*)"', h3.group(0), re.DOTALL)
        if href_match:
            link = href_match.group(1)
            # 搜狗链接可能是跳转格式，需要 follow
            if link.startswith("/link?url="):
                link = f"https://www.sogou.com{link}"
        # 提取摘要
        snippet = ""
        # 尝试匹配摘要容器
        for sm in re.finditer(r'<(?:p|span|div)[^>]*class="[^"]*(?:str-text|str_info|text-layout|space-txt)[^"]*"[^>]*>(.*?)</(?:p|span|div)>', block, re.DOTALL):
            t = _clean(sm.group(1))
            if len(t) > 15:
                snippet = t
                break
        if not snippet:
            # 备用：取第一个较长的文本段
            for sm in re.finditer(r'<(?:span|p|div)[^>]*>(.*?)</(?:span|p|div)>', block, re.DOTALL):
                t = _clean(sm.group(1))
                if len(t) > 25 and t != title:
                    snippet = t
                    break
        results.append({
            "title": title,
            "href": link,
            "url": link,
            "body": snippet[:300],
        })
        if len(results) >= max_results:
            break

    logger.info(f"[sogou] query='{query[:40]}' results={len(results)}")
    return results


# ── 百度搜索（替代搜狗作为主力搜索引擎） ──
async def _search_baidu(query: str, max_results: int = 10, timeout_s: float = 15) -> list:
    """搜索百度网页，解析 HTML 返回结构化结果列表。
    过滤掉百度图片、百度视频等非网页搜索结果。
    """
    from urllib.parse import quote_plus

    url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn={max_results}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text
    except httpx.TimeoutException:
        logger.warning(f"[baidu] 搜索超时: {query[:60]}")
        return []
    except Exception as e:
        logger.warning(f"[baidu] 搜索失败: {e}")
        return []

    # 轻量级 HTML 解析
    _TAG_RE = re.compile(r'<[^>]+>')
    _ENTITY_RE = re.compile(r'&(?:nbsp|amp|lt|gt|quot|#\d+|#x[\da-fA-F]+);')

    def _clean(text: str) -> str:
        text = _TAG_RE.sub('', text)
        text = _ENTITY_RE.sub(' ', text)
        return ' '.join(text.split())

    # 过滤非网页搜索结果（图片搜索、视频搜索等）
    _BAIDU_NOISE_RE = re.compile(r'image\.baidu\.com|/sf/vsearch\?|tn=baiduimage')

    results = []
    # 百度搜索结果以 class="result c-container" 或 class="c-container" 分隔
    blocks = re.split(
        r'<div[^>]*class="[^"]*(?:result\s+c-container|c-container)[^"]*"[^>]*>',
        html,
    )

    for block in blocks[1:]:  # 第一段是页面头部，跳过
        # 提取标题（<h3 class="t">）
        h3_match = re.search(
            r'<h3[^>]*class="[^"]*t[^"]*"[^>]*>(.*?)</h3>', block, re.DOTALL
        )
        if not h3_match:
            h3_match = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
        if not h3_match:
            continue
        title = _clean(h3_match.group(1))
        if not title:
            continue

        # 提取链接
        link = ""
        href_match = re.search(r'<a[^>]*href="([^"]*)"', h3_match.group(0))
        if href_match:
            link = href_match.group(1)

        # 过滤噪音结果（图片/视频聚合等）
        if _BAIDU_NOISE_RE.search(link) or _BAIDU_NOISE_RE.search(title):
            continue

        # 提取摘要：依次尝试多种百度摘要容器
        snippet = ""
        _snippet_patterns = [
            r'<span[^>]*class="[^"]*content-right_[^"]*"[^>]*>(.*?)</span>',
            r'<div[^>]*class="[^"]*c-abstract[^"]*"[^>]*>(.*?)</div>',
        ]
        for pat in _snippet_patterns:
            for sm in re.finditer(pat, block, re.DOTALL):
                t = _clean(sm.group(1))
                if len(t) > 30 and t != title:
                    snippet = t
                    break
            if snippet:
                break

        if not snippet:
            # 备用：取 block 中较长的纯文本段
            for sm in re.finditer(r'>([^<]{40,})<', block):
                t = sm.group(1).strip()
                if t and t != title and not t.startswith('{') and not t.startswith('var '):
                    snippet = t
                    break

        results.append({
            "title": title,
            "href": link,
            "url": link,
            "body": snippet[:300] if snippet else "",
        })
        if len(results) >= max_results:
            break

    logger.info(f"[baidu] query='{query[:40]}' results={len(results)}")
    return results


# ── 页面正文抓取（从搜索结果 URL 提取全文，用于高价值页面） ──
_HIGH_VALUE_DOMAINS = [
    "fandom.com", "baike.baidu.com", "wiki", "zh.wikipedia.org",
]
_PAGE_FETCH_TIMEOUT = 8        # 单页抓取超时（秒）
_PAGE_CONTENT_MAX_CHARS = 1500  # 单页正文最大字符数


async def _fetch_page_content(url: str, timeout_s: int = _PAGE_FETCH_TIMEOUT) -> tuple[str, str]:
    """抓取 URL 页面并提取正文文本。返回 (final_url, content)"""
    if not url or url.startswith("/sf/") or url.startswith("javascript:"):
        return ("", "")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    try:
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True,
        ) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return ("", "")
        final_url = str(resp.url)
        html = resp.text

        # 去 <script> <style>
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)

        # 标题
        title = ""
        for h_match in re.finditer(r'<h[12][^>]*>(.*?)</h[12]>', html, re.DOTALL):
            t = re.sub(r'<[^>]+>', '', h_match.group(1))
            t = re.sub(r'&\w+;', ' ', t).strip()
            if len(t) > 3:
                title = t
                break

        # 正文
        body_parts = []
        for p_match in re.finditer(r'<p[^>]*>(.*?)</p>', html, re.DOTALL):
            t = re.sub(r'<[^>]+>', '', p_match.group(1))
            t = re.sub(r'&\w+;', ' ', t).strip()
            t = ' '.join(t.split())
            if len(t) > 20 and t != title:
                body_parts.append(t)

        if len(''.join(body_parts)) < 80:
            for div_match in re.finditer(r'<div[^>]*>(.*?)</div>', html, re.DOTALL):
                t = re.sub(r'<[^>]+>', '', div_match.group(1))
                t = re.sub(r'&\w+;', ' ', t).strip()
                t = ' '.join(t.split())
                if len(t) > 40 and t not in body_parts:
                    body_parts.append(t)
                    if len(''.join(body_parts)) > _PAGE_CONTENT_MAX_CHARS:
                        break

        content = '\n'.join(body_parts)
        if title:
            content = f"【{title}】\n{content}"
        if len(content) > _PAGE_CONTENT_MAX_CHARS:
            content = content[:_PAGE_CONTENT_MAX_CHARS]
            last_period = max(content.rfind('。'), content.rfind('\n'))
            if last_period > _PAGE_CONTENT_MAX_CHARS * 0.6:
                content = content[: last_period + 1]
        return (final_url, content)
    except Exception as e:
        logger.debug(f"[fetch_page] 抓取失败 {url[:60]}: {e}")
        return ("", "")


def _is_high_value_url(url: str) -> bool:
    """判断 URL 是否属于值得抓取全文的高价值域名"""
    if not url:
        return False
    lower = url.lower()
    return any(domain in lower for domain in _HIGH_VALUE_DOMAINS)


# ── URL 提取与内容抓取 ──

_URL_PATTERN = re.compile(r'https?://[^\s<>]+')
_URL_MAX_FETCH = 2          # 每条消息最多抓取 URL 数
_URL_OVERALL_TIMEOUT = 10   # URL 处理总超时（秒）
_URL_CONTENT_MAX_CHARS = 2000  # URL 内容注入最大字符

_BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}


def _extract_urls(text: str) -> list[str]:
    """从文本中提取 URL，去重，最多返回 _URL_MAX_FETCH 个。"""
    urls = _URL_PATTERN.findall(text)
    seen = set()
    result = []
    for u in urls:
        u = u.rstrip('.,;:)!?\u3002\uff0c\uff01\uff1f\u3001\uff09')  # 去掉尾部标点
        if u not in seen:
            seen.add(u)
            result.append(u)
            if len(result) >= _URL_MAX_FETCH:
                break
    return result


def _parse_bvid(url: str) -> str | None:
    """从 B 站 URL 中提取 BV 号。"""
    m = re.search(r'(BV[a-zA-Z0-9]{10})', url)
    return m.group(1) if m else None


async def _fetch_bilibili_info(bvid: str) -> str | None:
    """通过 B 站 API 获取视频信息（标题、简介、标签、热评）。"""
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            # 第一步：获取视频信息
            info_resp = await client.get(
                f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                headers=_BILIBILI_HEADERS,
            )

            if info_resp.status_code != 200:
                return None
            data = info_resp.json()
            if data.get("code") != 0:
                return None

            video_info = data["data"]
            aid = video_info.get("aid")

            title = video_info.get("title", "")
            desc = video_info.get("desc", "")
            owner = video_info.get("owner", {}).get("name", "")
            stat = video_info.get("stat", {})
            view = stat.get("view", 0)
            like = stat.get("like", 0)
            coin = stat.get("coin", 0)
            danmaku = stat.get("danmaku", 0)
            reply_count = stat.get("reply", 0)

            # 格式化播放量
            if view >= 10000:
                view_str = f"{view / 10000:.1f}万"
            else:
                view_str = str(view)

            parts = [f"【B站视频】{title}"]
            parts.append(f"UP主：{owner}")
            parts.append(f"播放 {view_str} · 点赞 {like} · 投币 {coin} · 弹幕 {danmaku} · 评论 {reply_count}")

            if desc and desc.strip():
                desc_clean = desc.strip()
                if len(desc_clean) > 400:
                    desc_clean = desc_clean[:400] + "..."
                parts.append(f"\n简介：{desc_clean}")

            # 第二步：并行获取评论 + 标签
            comments = []
            comment_task = None
            tag_task = None

            if aid:
                comment_task = asyncio.create_task(
                    client.get(
                        f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&sort=1&ps=5",
                        headers=_BILIBILI_HEADERS,
                    )
                )
            tag_task = asyncio.create_task(
                client.get(
                    f"https://api.bilibili.com/x/tag/archive/tags?bvid={bvid}",
                    headers=_BILIBILI_HEADERS,
                )
            )

            # 等待评论和标签结果
            if comment_task:
                try:
                    cr = await comment_task
                    if cr.status_code == 200:
                        cdata = cr.json()
                        if cdata.get("code") == 0:
                            for r in (cdata.get("data", {}).get("replies", None) or [])[:5]:
                                msg = r.get("content", {}).get("message", "")
                                if msg and len(msg) > 5:
                                    uname = r.get("member", {}).get("uname", "")
                                    comments.append(f"  {uname}：{msg[:100]}")
                except Exception:
                    pass

            if comments:
                parts.append("\n热门评论：")
                parts.extend(comments)

            # 标签
            try:
                tag_resp = await tag_task
                if tag_resp.status_code == 200:
                    tag_data = tag_resp.json()
                    if tag_data.get("code") == 0:
                        tags = [t["tag_name"] for t in tag_data.get("data", [])[:8]]
                        if tags:
                            parts.append(f"\n标签：{', '.join(tags)}")
            except Exception:
                pass

            return "\n".join(parts)

    except Exception as e:
        logger.warning(f"[URL] B站 API 失败 {bvid}: {e}")
        return None


async def _resolve_short_url(url: str) -> str:
    """解析短链接，返回最终 URL。"""
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            resp = await client.head(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            return str(resp.url)
    except Exception:
        return url


async def _fetch_url_content(url: str) -> str | None:
    """抓取 URL 内容：B 站走专用 API，其他走通用页面抓取。"""
    lower = url.lower()

    # B 站短链先解析
    if "b23.tv" in lower:
        resolved = await _resolve_short_url(url)
        if "bilibili" in resolved.lower():
            bvid = _parse_bvid(resolved)
            if bvid:
                return await _fetch_bilibili_info(bvid)
        # 短链解析后不是 B 站，走通用抓取
        _, content = await _fetch_page_content(resolved)
        return content if content else None

    # B 站长链
    if "bilibili.com" in lower:
        bvid = _parse_bvid(url)
        if bvid:
            return await _fetch_bilibili_info(bvid)

    # 通用页面抓取
    _, content = await _fetch_page_content(url)
    return content if content else None


async def _process_message_urls(urls: list[str]) -> str | None:
    """处理消息中的所有 URL，返回拼接后的上下文文本。"""
    if not urls:
        return None

    tasks = [_fetch_url_content(u) for u in urls[:_URL_MAX_FETCH]]
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=_URL_OVERALL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[URL] 处理超时 ({_URL_OVERALL_TIMEOUT}s)")
        return None

    parts = []
    for url, result in zip(urls, results):
        if isinstance(result, str) and result:
            parts.append(f"[用户分享链接 {url} 的内容：]\n{result}")
        else:
            parts.append(f"[用户分享了一个链接 {url}，但未能获取内容]")

    combined = "\n\n".join(parts)
    if len(combined) > _URL_CONTENT_MAX_CHARS:
        combined = combined[:_URL_CONTENT_MAX_CHARS] + "\n...(内容过长已截断)"
    return combined


# ── DuckDuckGo Instant Answer API（免费，无需爬虫） ──
async def _search_ddg_instant(query: str, timeout_s: float = 8) -> list:
    """DuckDuckGo Instant Answer API，返回实体摘要和相关主题。"""
    from urllib.parse import quote_plus
    url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_html=1&skip_disambig=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = []

        abstract = data.get("AbstractText", "")
        if abstract and len(abstract) > 30:
            results.append({
                "title": data.get("Heading", query),
                "url": data.get("AbstractURL", ""),
                "body": abstract[:600],
                "source": "DDG",
            })

        for topic in data.get("RelatedTopics", [])[:5]:
            if "Text" in topic and len(topic["Text"]) > 20:
                results.append({
                    "title": topic["Text"][:60],
                    "url": topic.get("FirstURL", ""),
                    "body": topic["Text"][:400],
                    "source": "DDG",
                })
            elif "Topics" in topic:
                for sub in topic["Topics"][:3]:
                    if "Text" in sub and len(sub["Text"]) > 20:
                        results.append({
                            "title": sub["Text"][:60],
                            "url": sub.get("FirstURL", ""),
                            "body": sub["Text"][:400],
                            "source": "DDG",
                        })
        logger.info(f"[ddg] query='{query[:40]}' results={len(results)}")
        return results
    except Exception as e:
        logger.warning(f"[ddg] 搜索失败: {e}")
        return []


async def _execute_web_search(query: str, max_results: int = 5) -> str:
    """异步执行联网搜索：百度主力 + 微博补充 + 搜狗兜底 + 页面内容抓取。

    搜索策略：
    1. 第一轮（并行）：百度 + 微博
    2. 页面增强：对疑似百科类页面并行抓取正文
    3. 兜底：若百度无结果，启用搜狗
    """

    _TIMEOUT = 15  # 搜索超时（秒）

    # ── 带超时的百度搜索 ──
    async def _baidu_with_timeout(q: str, max_r: int) -> tuple[str, list]:
        try:
            res = await asyncio.wait_for(
                _search_baidu(q, max_results=max_r, timeout_s=_TIMEOUT),
                timeout=_TIMEOUT + 3,
            )
            return (q, res)
        except asyncio.TimeoutError:
            logger.warning(f"[web_search] 百度搜索超时 ({_TIMEOUT}s): {q[:60]}")
            return (q, [])
        except Exception as e:
            logger.warning(f"[web_search] 百度搜索失败: {e}")
            return (q, [])

    # ── 带超时的搜狗搜索（兜底用） ──
    async def _sogou_with_timeout(q: str, max_r: int) -> tuple[str, list]:
        try:
            res = await asyncio.wait_for(
                _search_sogou(q, max_results=max_r, timeout_s=12),
                timeout=14,
            )
            return (q, res)
        except asyncio.TimeoutError:
            logger.warning(f"[web_search] 搜狗搜索超时: {q[:60]}")
            return (q, [])
        except Exception as e:
            logger.warning(f"[web_search] 搜狗搜索失败: {e}")
            return (q, [])

    # ── 微博搜索降级链 ──
    _weibo_cookie_expired = False  # 追踪 cookie 状态

    async def _weibo_wrapper() -> tuple[str, list]:
        nonlocal _weibo_cookie_expired
        # 检查 cookie 是否存在
        cookie_data = _load_weibo_cookies()
        if not cookie_data:
            _weibo_cookie_expired = True
            return ("weibo_direct", [])

        res = await _search_weibo_direct(query, max_results=8, timeout_s=_TIMEOUT)
        if res:
            return ("weibo_direct", res)
        # 检查是否因 cookie 过期导致无结果
        if cookie_data and not res:
            _weibo_cookie_expired = True  # 标记可能过期
        simplified = _extract_core_keywords(query)
        if simplified != query:
            res = await _search_weibo_direct(simplified, max_results=8, timeout_s=_TIMEOUT)
            if res:
                _weibo_cookie_expired = False  # 简化词有结果，cookie 正常
                logger.info(f"[weibo] 简化关键词命中: '{query}' → '{simplified}'")
                return ("weibo_direct", res)
        logger.info(f"[weibo] 所有搜索均未命中: query='{query}'")
        return ("weibo_direct", [])

    # 检查健康状态
    baidu_ok = _is_search_healthy("baidu")
    sogou_ok = _is_search_healthy("sogou")
    weibo_ok = _is_search_healthy("weibo")
    if not baidu_ok:
        logger.info("[web_search] 百度不健康，跳过")
    if not sogou_ok:
        logger.info("[web_search] 搜狗不健康，跳过")
    if not weibo_ok:
        logger.info("[web_search] 微博不健康，跳过")

    # ── 第一轮：百度 + 微博 并行 ──
    round1_tasks = []
    if baidu_ok:
        round1_tasks.append(_baidu_with_timeout(query, max_results + 5))
    if weibo_ok:
        round1_tasks.append(_weibo_wrapper())
    round1_results = await asyncio.gather(*round1_tasks) if round1_tasks else []

    results = []
    seen_urls = set()
    baidu_total = 0
    weibo_total = 0

    for _q, items in round1_results:
        if _q == "weibo_direct":
            weibo_total = len(items)
            for r in items:
                url = r.get("href") or r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(("微博", r))
        else:
            baidu_total = len(items)
            for r in items:
                url = r.get("href") or r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(("百度", r))

    # 更新健康状态
    if baidu_ok:
        _update_search_health("baidu", baidu_total > 0)
    if weibo_ok:
        _update_search_health("weibo", weibo_total > 0)

    # ── 第二轮：搜狗兜底（仅百度无结果时） ──
    sogou_total = 0
    if baidu_total == 0 and sogou_ok:
        logger.info("[web_search] 百度无结果，启用搜狗兜底")
        _, sogou_items = await _sogou_with_timeout(query, max_results + 3)
        sogou_total = len(sogou_items)
        for r in sogou_items:
            url = r.get("href") or r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(("搜狗", r))
        _update_search_health("sogou", sogou_total > 0)

    if not results:
        return "未找到相关结果。"

    # ── 页面内容增强 ──
    # 选取疑似百科/资料页的结果进行全文抓取。
    # 百度链接是重定向链接，通过标题关键词判断是否值得抓取。
    _ENRICH_TITLE_KEYWORDS = [
        "wiki", "百科", "fandom", "维基百科", "百度百",
        "官方", "官网", "简介", "资料", "profile",
    ]

    def _should_enrich(r: dict) -> bool:
        """判断搜索结果是否值得抓取全文"""
        title = (r.get("title") or "").lower()
        url = (r.get("href") or r.get("url") or "").lower()
        if _is_high_value_url(url):
            return True
        return any(kw in title for kw in _ENRICH_TITLE_KEYWORDS)

    fetch_targets = []  # [(index_in_results, url)]
    for idx, (source, r) in enumerate(results):
        url = r.get("href") or r.get("url", "")
        if url and _should_enrich(r) and len(fetch_targets) < 3:
            fetch_targets.append((idx, url))

    page_contents = {}  # index → page_text
    if fetch_targets:
        fetch_tasks = [_fetch_page_content(url) for _, url in fetch_targets]
        fetch_results = await asyncio.gather(*fetch_tasks)
        for (idx, url), (final_url, content) in zip(fetch_targets, fetch_results):
            if content and len(content) > 80:
                # 二次过滤：抓取后检查最终 URL 是否真的高价值
                if _is_high_value_url(final_url) or len(content) > 200:
                    page_contents[idx] = content
                if final_url:
                    seen_urls.add(final_url)
        logger.info(
            f"[web_search] 页面增强: 抓取 {len(fetch_targets)} 页, "
            f"有效 {len(page_contents)} 页"
        )

    # ── 结果去重与排序 ──
    # 按 URL 去重（保留第一个出现的）
    seen_final = set()
    deduped_results = []
    for source, r in results:
        url = (r.get("href") or r.get("url", "")).rstrip('/')
        if url and url not in seen_final:
            seen_final.add(url)
            deduped_results.append((source, r))
    results = deduped_results

    # 按相关性排序：标题包含查询关键词的排前面
    query_lower = query.lower()
    def _relevance_score(item: tuple) -> int:
        source, r = item
        score = 0
        title = (r.get("title") or "").lower()
        body = (r.get("body") or r.get("snippet", "")).lower()
        # 标题命中关键词 +3
        for kw in query_lower.split():
            if kw in title:
                score += 3
        # 摘要命中关键词 +1
        for kw in query_lower.split():
            if kw in body:
                score += 1
        # 百科类来源 +2
        if any(kw in title for kw in ["百科", "wiki", "维基"]):
            score += 2
        return score
    results.sort(key=_relevance_score, reverse=True)

    # 截取到合理数量
    results = results[:max_results * 2]

    # ── 格式化输出 ──
    lines = [f"## 网络搜索结果（百度 {baidu_total} + 微博 {weibo_total}）\n"]

    # 微博 cookie 过期提示
    if _weibo_cookie_expired and weibo_total == 0:
        lines.append("⚠️ 微博搜索不可用（Cookie 可能已过期，请在 Dashboard 上传新的 cookies.json）\n")

    for i, (source, r) in enumerate(results, 1):
        title = r.get("title", "无标题")
        url = r.get("href") or r.get("url", "")
        body = r.get("body") or r.get("snippet", "")
        tag = f" [{source}]" if source != "百度" else ""

        # 如果有页面正文，优先展示正文（更丰富）
        page_text = page_contents.get(i - 1, "")
        if page_text:
            lines.append(
                f"[{i}] {title}{tag}\n"
                f"    链接: {url}\n"
                f"    【页面内容】:\n{page_text}\n"
            )
        else:
            lines.append(f"[{i}] {title}{tag}\n    链接: {url}\n    摘要: {body}\n")

    return "\n".join(lines)


def _execute_corpus_search(skill_name: str, keywords: str) -> str:
    """在指定 skill 目录下搜索 .md 和 corpus_ref/ 下的文本文件，返回匹配的上下文片段。
    搜索范围：
      - 根目录 *.md（排除 backup_* 目录）
      - corpus_ref/*.txt（微博语料等，按关键词命中数打分排序）
      - corpus_ref/weibo_profile_detail.json（自动提取账号简介）
    """
    skill_dir = SKILLS_DIR / skill_name
    if not skill_dir.exists():
        return f"角色 '{skill_name}' 的语料库不存在"

    # 拆分关键词，按长度降序排列（长关键词更具体，权重更高）
    kw_list = [k.strip().lower() for k in keywords.split() if k.strip()]
    if not kw_list:
        return "未提供有效关键词"
    kw_list.sort(key=len, reverse=True)

    # 计算关键词 IDF 权重：越短的关键词越常见，权重越低
    kw_weight: dict[str, float] = {}
    for kw in kw_list:
        if len(kw) <= 2:
            kw_weight[kw] = 0.5
        elif len(kw) <= 4:
            kw_weight[kw] = 1.0
        else:
            kw_weight[kw] = 2.0

    _MAX_SNIPPETS = 12
    results: list[str] = []

    def _match_line(line_lower: str) -> bool:
        return any(kw in line_lower for kw in kw_list)

    def _score_line(line_lower: str) -> float:
        """计算一行文本的关键词匹配得分。命中越多/越具体的关键词，得分越高。"""
        score = 0.0
        for kw in kw_list:
            if kw in line_lower:
                score += kw_weight.get(kw, 1.0)
        return score

    # ── 1) 根目录 *.md 文件（跳过 backup_* 目录） ──
    for md_file in sorted(skill_dir.glob("*.md")):
        if len(results) >= _MAX_SNIPPETS:
            break
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if _match_line(line.lower()):
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                snippet = "\n".join(lines[start:end]).strip()
                results.append(f"[{md_file.name} 第{i+1}行]\n{snippet}")
                if len(results) >= _MAX_SNIPPETS:
                    break

    # ── 2) corpus_ref/*.txt 文件（微博语料等）──
    # 使用 TF-IDF 风格打分：语料中出现越少的关键词权重越高
    # 对上下文窗口整体打分，排序后取 top-N，相邻行去重
    corpus_ref_dir = skill_dir / "corpus_ref"
    txt_hits: list[tuple[float, int, str]] = []   # (score, line_idx, formatted_snippet)
    if corpus_ref_dir.is_dir():
        for txt_file in sorted(corpus_ref_dir.glob("*.txt")):
            try:
                content = txt_file.read_text(encoding="utf-8")
            except Exception:
                continue
            lines = content.split("\n")
            total_lines = max(len(lines), 1)

            # 统计每个关键词在 txt 中的文档频率（多少行包含该关键词）
            _kw_df: dict[str, int] = {}
            for kw in kw_list:
                df = sum(1 for ln in lines if kw in ln.lower())
                _kw_df[kw] = df

            # IDF 权重：df 越小（越稀有）权重越高
            _kw_idf: dict[str, float] = {}
            for kw, df in _kw_df.items():
                if df > 0:
                    _kw_idf[kw] = math.log(total_lines / df)
                else:
                    _kw_idf[kw] = 0.0  # 语料中不存在，不计分

            def _score_line_idf(context_lower: str) -> float:
                """基于 IDF 的上下文打分"""
                return sum(_kw_idf.get(kw, 0.0) for kw in kw_list if kw in context_lower)

            for i, line in enumerate(lines):
                line_lower = line.lower()
                if _match_line(line_lower):
                    start = max(0, i - 4)
                    end = min(len(lines), i + 5)
                    context_lower = "\n".join(lines[start:end]).lower()
                    score = _score_line_idf(context_lower)
                    snippet = "\n".join(lines[start:end]).strip()
                    txt_hits.append((score, i, f"[{txt_file.name} 第{i+1}行]\n{snippet}"))

    # 按得分降序排序，取前 _MAX_TXT_SNIPPETS 条，相邻行去重
    _MAX_TXT_SNIPPETS = 6
    txt_hits.sort(key=lambda x: x[0], reverse=True)
    _seen_line_idxs: list[int] = []
    _txt_added = 0
    for score, line_idx, snippet in txt_hits:
        if _txt_added >= _MAX_TXT_SNIPPETS or len(results) >= _MAX_SNIPPETS:
            break
        # 去重：跳过与已选结果行号距离 < 6 的条目
        if any(abs(line_idx - s) < 6 for s in _seen_line_idxs):
            continue
        _seen_line_idxs.append(line_idx)
        results.append(snippet)
        _txt_added += 1

    # ── 3) 微博账号简介（自动附加，不需要关键词命中） ──
    profile_file = corpus_ref_dir / "weibo_profile_detail.json" if corpus_ref_dir.is_dir() else None
    if profile_file and profile_file.exists():
        try:
            pdata = json.loads(profile_file.read_text(encoding="utf-8"))
            user_info = pdata.get("data", {}).get("user", {})
            profile_parts = []
            if user_info.get("screen_name"):
                profile_parts.append(f"微博名: {user_info['screen_name']}")
            if user_info.get("verified_reason"):
                profile_parts.append(f"认证: {user_info['verified_reason']}")
            if user_info.get("description"):
                profile_parts.append(f"简介: {user_info['description']}")
            if user_info.get("location"):
                profile_parts.append(f"地区: {user_info['location']}")
            if user_info.get("gender"):
                g = {"m": "男", "f": "女"}.get(user_info["gender"], user_info["gender"])
                profile_parts.append(f"性别: {g}")
            if profile_parts:
                profile_text = "\n".join(profile_parts)
                results.insert(0, f"[微博档案]\n{profile_text}")
        except Exception:
            pass

    if not results:
        return f"在角色 '{skill_name}' 的语料库中未找到与「{keywords}」相关的内容"

    return f"## 语料库搜索结果（{skill_name}）\n\n" + "\n\n---\n\n".join(results)


def _get_http_client() -> httpx.AsyncClient:
    """获取或创建持久化 HTTP 客户端"""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=15),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _http_client


async def _api_request(payload: dict, provider: dict = None) -> Optional[dict]:
    """底层 API 请求，带重试和 provider 回退。返回 JSON dict 或 None"""
    if provider is None:
        provider = _get_active_provider()

    result = await _api_request_inner(payload, provider)
    if result is not None:
        _mark_provider_healthy(provider["name"])
        return result

    # 主 provider 失败，尝试回退
    fallback_name = "deepseek" if provider["name"] == "openai" else None
    if fallback_name:
        _mark_provider_failed(provider["name"])
        fallback = _get_provider(fallback_name)
        chat_logger.info(f"[LLM] {provider['name']} 失败，回退到 {fallback_name}")
        result = await _api_request_inner(payload, fallback)
        if result is not None:
            _mark_provider_healthy(fallback["name"])
    return result


async def _api_request_inner(payload: dict, provider: dict) -> Optional[dict]:
    """对指定 provider 发送 API 请求，带重试。返回 JSON dict 或 None"""
    url = f"{provider['base_url']}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
    }
    adapted = _adapt_payload_for_provider(payload, provider["name"])
    client = _get_http_client()
    last_error = None
    pname = provider["name"]

    for attempt in range(1, _API_MAX_RETRIES + 1):
        try:
            resp = await client.post(url, headers=headers, json=adapted)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            body = e.response.text[:200]
            last_error = f"HTTP {status}: {body}"
            if status in (429, 500, 502, 503, 529) and attempt < _API_MAX_RETRIES:
                delay = _API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(f"[{pname}] API {status} (attempt {attempt}/{_API_MAX_RETRIES}), retry in {delay}s: {body}")
                await asyncio.sleep(delay)
                continue
            logger.error(f"[{pname}] API HTTP error (attempt {attempt}/{_API_MAX_RETRIES}): {last_error}")
        except httpx.TimeoutException as e:
            last_error = f"Timeout: {e}"
            if attempt < _API_MAX_RETRIES:
                delay = _API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(f"[{pname}] API timeout (attempt {attempt}/{_API_MAX_RETRIES}), retry in {delay}s")
                await asyncio.sleep(delay)
                continue
            logger.error(f"[{pname}] API timeout (attempt {attempt}/{_API_MAX_RETRIES}): {e}")
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.error(f"[{pname}] API unexpected error (attempt {attempt}/{_API_MAX_RETRIES}): {last_error}")
            if attempt < _API_MAX_RETRIES:
                await asyncio.sleep(_API_RETRY_BASE_DELAY)
                continue

    logger.error(f"[{pname}] API failed after {_API_MAX_RETRIES} attempts: {last_error}")
    return None


async def _api_request_stream_inner(payload: dict, provider: dict):
    """对指定 provider 的流式 API 请求。成功 yield True，全部重试耗尽 yield False。"""
    url = f"{provider['base_url']}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
    }
    adapted = _adapt_payload_for_provider(payload, provider["name"])
    adapted = {**adapted, "stream": True}
    client = _get_http_client()
    pname = provider["name"]

    for attempt in range(1, _API_MAX_RETRIES + 1):
        try:
            async with client.stream("POST", url, headers=headers, json=adapted) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    body_text = body.decode("utf-8", errors="replace")[:200]
                    if resp.status_code in (429, 500, 502, 503, 529) and attempt < _API_MAX_RETRIES:
                        delay = _API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                        logger.warning(
                            f"[{pname}] stream API {resp.status_code} "
                            f"(attempt {attempt}/{_API_MAX_RETRIES}), retry in {delay}s"
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.error(f"[{pname}] stream API HTTP {resp.status_code}: {body_text}")
                    yield False
                    return

                buffer = ""
                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            yield True
                            return
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
            yield True  # stream ended normally
            return

        except httpx.TimeoutException as e:
            if attempt < _API_MAX_RETRIES:
                delay = _API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"[{pname}] stream timeout "
                    f"(attempt {attempt}/{_API_MAX_RETRIES}), retry in {delay}s"
                )
                await asyncio.sleep(delay)
                continue
            logger.error(f"[{pname}] stream timeout after {_API_MAX_RETRIES} attempts: {e}")
            yield False
            return
        except Exception as e:
            if attempt < _API_MAX_RETRIES:
                await asyncio.sleep(_API_RETRY_BASE_DELAY)
                continue
            logger.error(f"[{pname}] stream unexpected error: {e}")
            yield False
            return


async def _api_request_stream(payload: dict):
    """流式 API 请求，带 provider 回退。优先使用活跃 provider，失败后回退 DeepSeek。"""
    provider = _get_active_provider()
    sent_any = False
    result = None

    async for chunk in _api_request_stream_inner(payload, provider):
        if chunk is True:
            _mark_provider_healthy(provider["name"])
            return
        elif chunk is False:
            result = False
            break
        else:
            sent_any = True
            yield chunk

    # 如果已经发送了内容给调用方，不再尝试回退
    if sent_any:
        return

    # 主 provider 失败且未产出任何内容，尝试回退
    fallback_name = "deepseek" if provider["name"] == "openai" else None
    if fallback_name and result is False:
        _mark_provider_failed(provider["name"])
        fallback = _get_provider(fallback_name)
        chat_logger.info(f"[LLM] stream: {provider['name']} 失败，回退到 {fallback_name}")
        async for chunk in _api_request_stream_inner(payload, fallback):
            if chunk is True:
                _mark_provider_healthy(fallback["name"])
                return
            elif chunk is False:
                return
            else:
                yield chunk


# ── DeepSeek DSML 标记清洗 ──
# DeepSeek 有时会在 content 中嵌入工具调用的 XML 标记（｜｜DSML｜｜tool_calls...），
# 需要在使用 tools 时清洗掉这些原始标记，避免泄露到最终回复。
_DSML_PATTERN = re.compile(
    r'[\uff5c|]*\s*DSML\s*[\uff5c|]*\s*tool_calls\s*>.*?'
    r'[\uff5c|]*\s*/?\s*DSML\s*[\uff5c|]*\s*/?\s*tool_calls\s*>',
    re.DOTALL
)
_DSML_INVOKE_PATTERN = re.compile(
    r'[\uff5c|]*\s*DSML\s*[\uff5c|]*\s*invoke[^>]*>.*?'
    r'[\uff5c|]*\s*/?\s*DSML\s*[\uff5c|]*\s*/?\s*invoke\s*>',
    re.DOTALL
)
_DSML_PARAM_PATTERN = re.compile(
    r'[\uff5c|]*\s*DSML\s*[\uff5c|]*\s*parameter[^>]*>.*?'
    r'[\uff5c|]*\s*/?\s*DSML\s*[\uff5c|]*\s*/?\s*parameter\s*>',
    re.DOTALL
)
# 通用兜底：匹配所有包含 DSML 的尖括号标签
_DSML_ANY_TAG = re.compile(r'<[^>]*DSML[^>]*/?>',  re.IGNORECASE)
# 截断兜底：匹配从 DSML 开标签到字符串末尾（处理模型输出被截断的情况）
_DSML_TRUNCATED = re.compile(
    r'<[\uff5c|]*\s*DSML\s*[\uff5c|].*',
    re.DOTALL
)
# ── 原生 tool_calls XML 清洗 ──
# 流式路径不带 tools 定义，但模型有时会模仿历史中的 function calling 格式，
# 输出原生 <tool_calls>...</tool_calls> XML 片段，必须清除以避免泄露给用户。
_TOOL_CALLS_FULL = re.compile(
    r'<tool_calls>.*?</tool_calls>',
    re.DOTALL
)
# 截断的 tool_call 开头：<tool_call... （模型输出被截断）
_TOOL_CALL_TRUNCATED = re.compile(
    r'<tool_call[^>]*>.*',
    re.DOTALL
)
# 最终兜底：从 <tool_calls> 开标签到字符串末尾（处理工具调用中被截断的情况）
_TOOL_CALLS_OPEN_TRUNC = re.compile(
    r'<tool_calls>.*',
    re.DOTALL
)
# ── 通用工具调用标签清洗 ──
# 模型在流式路径（无 tools 定义）中可能模仿 function calling 格式，
# 输出 <web_search query="...">、</web_search>、<search_corpus ... /> 等标签。
_TOOL_LIKE_TAG = re.compile(
    r'</?\s*(?:web_search|search_corpus|search)\b[^>]*/?>',
    re.IGNORECASE
)


def _strip_dsml_markup(text: str) -> str:
    """清洗 DeepSeek 可能嵌入 content 的工具调用标记（DSML 和原生 XML）"""
    # DSML 格式
    text = _DSML_PATTERN.sub('', text)
    text = _DSML_INVOKE_PATTERN.sub('', text)
    text = _DSML_PARAM_PATTERN.sub('', text)
    text = _DSML_ANY_TAG.sub('', text)
    # 原生 <tool_calls> XML 格式
    text = _TOOL_CALLS_FULL.sub('', text)
    text = _TOOL_CALL_TRUNCATED.sub('', text)
    # 通用工具调用标签（GPT-5.5 等模型模仿 function calling）
    text = _TOOL_LIKE_TAG.sub('', text)
    # 截断兜底：如果还有残留的 DSML / tool_calls 开标签，删除从那里到末尾
    text = _DSML_TRUNCATED.sub('', text)
    text = _TOOL_CALLS_OPEN_TRUNC.sub('', text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _clean_llm_reply(raw_content: str) -> str:
    """清洗模型回复，处理截断的 DSML 标记等异常情况。

    模型在工具调用轮次后可能仍尝试生成 DSML 标记，但因 max_tokens
    截断而只残留不完整的尖括号内容（如 '<'）。此函数负责清理这些
    残留并保证返回有意义的文本。
    """
    cleaned = _strip_dsml_markup(raw_content)
    # 去除尾部残留的不完整 XML-like 标签（如 '<'、'</'、'<tag' 无闭合 '>'）
    cleaned = re.sub(r'<[^>]*$', '', cleaned).strip()
    # 如果清洗后内容过短或无实质内容，返回兜底消息
    if len(cleaned) <= 1 or not re.search(r'[\w\u4e00-\u9fff]', cleaned):
        chat_logger.warning(f"[LLM] 模型回复清洗后为空或无意义 (原始: {repr(raw_content[:200])})")
        return "呜呜对不起！刚刚脑袋打了个盹儿 [捂脸] 泥再说一遍问题好不好？🥺"
    return cleaned


async def _probe_tool_usage(system_prompt: str, messages: list[dict], skill_name: str = "") -> bool:
    """快速探测模型是否需要调用工具。
    返回 True 表示模型想调用工具，False 表示直接回复。
    """
    use_tools = WEB_SEARCH_ENABLED and _SEARCH_AVAILABLE
    if not use_tools and not skill_name:
        return False  # 没有可用工具

    active_tools = []
    if use_tools:
        active_tools.append(_SEARCH_TOOLS[0])   # web_search
    if skill_name:
        active_tools.append(_SEARCH_TOOLS[1])   # search_corpus

    # 注入日期（与正式调用一致）
    now = datetime.now()
    date_hint = f"\n\n[当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{['一','二','三','四','五','六','日'][now.weekday()]}]"
    probe_system = system_prompt + date_hint

    payload = {
        "model": DEEPSEEK_SEARCH_MODEL,
        "reasoning_effort": "low",
        "temperature": 0.3,           # 低温度，更稳定的决策
        "top_p": 0.9,
        "max_tokens": 10,             # 增加到 10，让模型有足够空间表达意图
        "frequency_penalty": 0.3,
        "presence_penalty": 0.2,
        "stream": False,
        "messages": [{"role": "system", "content": probe_system}] + messages,
        "tools": active_tools,
        "tool_choice": "auto",
    }

    try:
        await asyncio.wait_for(_API_SEMAPHORE.acquire(), timeout=10)
    except asyncio.TimeoutError:
        chat_logger.warning("[PROBE] API 并发已满，跳过探测")
        return True  # 假设需要工具，走安全路径

    try:
        # 探测始终使用 DeepSeek（function calling 兼容性最好，且 flash 模型速度快）
        data = await _api_request(payload, _get_provider("deepseek"))
    finally:
        _API_SEMAPHORE.release()

    if data is None:
        return True  # API 失败，走安全的非流式路径

    tool_calls = data["choices"][0]["message"].get("tool_calls")
    wants_tools = bool(tool_calls)
    chat_logger.info(f"[PROBE] wants_tools={wants_tools}")
    return wants_tools


async def call_deepseek(system_prompt: str, messages: list[dict], skill_name: str = "") -> Optional[str]:
    """调用 DeepSeek API，支持 Function Calling 联网搜索 + 语料库搜索"""
    # 全局并发控制：最多 3 个请求同时进行，超出的排队等待（最多等 30 秒）
    try:
        await asyncio.wait_for(_API_SEMAPHORE.acquire(), timeout=30)
    except asyncio.TimeoutError:
        chat_logger.warning("[LLM] API 并发已满，排队超时")
        return "等一下下哦！窝的小脑袋瓜正在疯狂运转中 [捂脸] 再过几秒来戳窝叭～"

    try:
        return await asyncio.wait_for(
            _call_deepseek_inner(system_prompt, messages, skill_name),
            timeout=120,  # 整体超时 2 分钟，防止搜索循环无限卡住
        )
    except asyncio.TimeoutError:
        chat_logger.warning("[LLM] 整体调用超时 (180s)，强制返回")
        return "网络君跑不动惹！好慢好慢 [流汗] 过一会儿再戳窝叭拜托拜托 🙏💦"
    finally:
        _API_SEMAPHORE.release()


async def _call_deepseek_inner(system_prompt: str, messages: list[dict], skill_name: str = "") -> Optional[str]:
    """实际的 DeepSeek API 调用逻辑（由 call_deepseek 的 Semaphore 保护）"""
    use_tools = WEB_SEARCH_ENABLED and _SEARCH_AVAILABLE
    # 语料库搜索始终可用，联网搜索取决于配置
    tools_available = use_tools or bool(skill_name)

    # 选择 provider 和模型
    if tools_available:
        # 工具调用场景强制使用 DeepSeek（function calling 兼容性最好）
        provider = _get_provider("deepseek")
        active_model = DEEPSEEK_SEARCH_MODEL
    else:
        provider = _get_active_provider()
        active_model = provider["model"]

    reasoning_effort = "low" if tools_available else "high"
    chat_logger.info(f"[LLM] 调用 {provider['name']} | model={active_model} | reasoning={reasoning_effort} | 联网={'开' if use_tools else '关'} | 语料库={'开' if skill_name else '关'} | msgs={len(messages)}")

    # 联网搜索时注入当前日期，避免模型猜错时间
    if use_tools:
        now = datetime.now()
        date_hint = f"\n\n[当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{['一','二','三','四','五','六','日'][now.weekday()]}]"
        system_prompt = system_prompt + date_hint

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    base_payload = {
        "model": active_model,
        "reasoning_effort": reasoning_effort,
        "temperature": 0.95,
        "top_p": 0.9,
        "max_tokens": 2048,
        "frequency_penalty": 0.3,
        "presence_penalty": 0.2,
        "stream": False,
    }

    if not tools_available:
        # 不带 tools 的简单调用
        payload = {**base_payload, "messages": full_messages}
        data = await _api_request(payload, provider)
        if data is None:
            return None
        return _clean_llm_reply(data["choices"][0]["message"]["content"].strip())

    # 根据可用能力动态构建工具列表
    active_tools = []
    if use_tools:
        active_tools.append(_SEARCH_TOOLS[0])   # web_search
    if skill_name:
        active_tools.append(_SEARCH_TOOLS[1])   # search_corpus

    # ── Function Calling 循环 ──
    for round_num in range(_MAX_TOOL_ROUNDS + 1):
        payload = {
            **base_payload,
            "messages": full_messages,
            "tools": active_tools,
            "tool_choice": "auto",
        }

        data = await _api_request(payload, provider)
        if data is None:
            return None

        choice = data["choices"][0]
        message = choice["message"]

        # 检查是否有工具调用
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            # 无工具调用 → 直接返回（同时清洗可能残留的 DSML 标记）
            raw_content = message.get("content", "").strip()
            chat_logger.info(f"[LLM] round={round_num} | 模型直接回复（未调用工具）")
            return _clean_llm_reply(raw_content)

        # 把模型的 tool_calls 消息追加到历史
        chat_logger.info(f"[LLM] round={round_num} | 模型调用工具: {[tc['function']['name'] for tc in tool_calls]}")
        full_messages.append(message)

        # 处理每个工具调用
        for tc in tool_calls:
            func_name = tc["function"]["name"]
            func_args = json.loads(tc["function"]["arguments"])

            if func_name == "web_search":
                query = func_args.get("query", "")
                max_results = func_args.get("max_results", 8)
                logger.info(f"[search] 联网搜索: {query} (max={max_results})")
                chat_logger.info(f"[SEARCH] 联网搜索触发: query='{query}', max_results={max_results}")
                search_text = await _execute_web_search(query, max_results)

            elif func_name == "search_corpus":
                kw = func_args.get("keywords", "")
                chat_logger.info(f"[CORPUS] 语料库搜索: skill='{skill_name}', keywords='{kw}'")
                search_text = _execute_corpus_search(skill_name, kw)

            else:
                search_text = f"未知工具: {func_name}"

            full_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": search_text,
            })

    # 超过最大轮次，强制让模型回复（不带 tools）
    # 追加一条提示，让模型基于已有搜索结果生成回复，而不是继续尝试搜索
    _has_search_results = any(
        m["role"] == "tool" and "未找到" not in m.get("content", "")
        for m in full_messages
    )
    if _has_search_results:
        full_messages.append({
            "role": "user",
            "content": "（系统提示：请根据上面搜索到的信息直接回答用户的问题，不要再调用搜索工具。如果搜索结果不够完整，也请基于已有信息给出尽可能的回答。）",
        })

    payload_final = {**base_payload, "messages": full_messages}
    data = await _api_request(payload_final, provider)
    if data is None:
        return None
    raw_content = data["choices"][0]["message"]["content"].strip()
    reply = _clean_llm_reply(raw_content)

    # 如果回复仍为空（DSML 截断），用搜索结果做最后一次尝试
    if "脑子短路" in reply and _has_search_results:
        chat_logger.info("[LLM] 回复被 DSML 截断，追加总结重试")
        full_messages.append({
            "role": "user",
            "content": "（系统：请立刻用自然语言总结上面搜索到的内容回复用户，不要使用任何工具标记。）",
        })
        payload_retry = {**base_payload, "messages": full_messages}
        data2 = await _api_request(payload_retry, provider)
        if data2:
            raw2 = data2["choices"][0]["message"]["content"].strip()
            reply2 = _clean_llm_reply(raw2)
            if "脑子短路" not in reply2:
                return reply2

    return reply


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
