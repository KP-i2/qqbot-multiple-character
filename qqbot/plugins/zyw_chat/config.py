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
