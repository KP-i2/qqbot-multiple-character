"""
全局配置、路径常量、日志、运行时设置
"""

import json
import logging
import os
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from nonebot import get_driver, logger


# ============================================================
# 路径常量
# ============================================================

PLUGIN_DIR = Path(__file__).parent
QQBOT_DIR = PLUGIN_DIR.parent.parent
SKILLS_DIR = QQBOT_DIR / "skills"
CORPUS_DIR = QQBOT_DIR.parent / "corpus"
PHOTO_DIR = QQBOT_DIR.parent / "photo"
NORMAL_PAPER_DIR = QQBOT_DIR.parent / "normal-paper"


# ============================================================
# API 配置
# ============================================================

driver = get_driver()
config = driver.config

DEEPSEEK_API_KEY: str = getattr(config, "deepseek_api_key", "")
DEEPSEEK_BASE_URL: str = getattr(config, "deepseek_base_url", "https://api.deepseek.com")
DEEPSEEK_MODEL: str = getattr(config, "deepseek_model", "deepseek-v4-pro")
DEEPSEEK_SEARCH_MODEL: str = "deepseek-v4-flash"  # 联网搜索时使用更快的模型

# OpenAI API（主选模型，失败时回退 DeepSeek）
_openai_enabled_raw = getattr(config, "openai_enabled", "false")
OPENAI_ENABLED: bool = str(_openai_enabled_raw).lower() == "true"
OPENAI_API_KEY: str = getattr(config, "openai_api_key", "")
OPENAI_BASE_URL: str = getattr(config, "openai_base_url", "")
OPENAI_MODEL: str = getattr(config, "openai_model", "gpt-5.5")


# ============================================================
# 通用参数
# ============================================================

# 活跃时间开关
ACTIVE_HOURS_START: int = int(getattr(config, "active_hours_start", 0))
ACTIVE_HOURS_END: int = int(getattr(config, "active_hours_end", 23))

# Web Search 功能
_web_search_raw = getattr(config, "web_search_enabled", "true")
WEB_SEARCH_ENABLED: bool = str(_web_search_raw).lower() == "true" if _web_search_raw is not None else True

# 搜狗搜索（替代 DuckDuckGo/百度，国内直连无需第三方库）
SEARCH_AVAILABLE = True

# 历史与超时
MAX_HISTORY_ROUNDS = 40
REQUEST_TIMEOUT = 60
MAX_HISTORY_MESSAGES = MAX_HISTORY_ROUNDS * 2

# 管理员
ADMIN_QQ = getattr(config, "admin_qq", "")
DEFAULT_SKILL_NAME_CONFIG: str = getattr(config, "default_skill", "")


# ============================================================
# 搜索意图关键词
# ============================================================

SEARCH_INTENT_KEYWORDS = [
    "查一下", "搜一下", "帮我查", "帮我搜", "搜索", "查找", "查查",
    "了解一下", "找找", "看看", "是什么", "是谁", "谁啊", "哪个团",
    "什么团", "哪里的", "介绍一下", "介绍下", "介绍", "科普", "百科", "资料",
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
    "关于", "说说", "讲讲", "网上", "有介绍",
    # 链接/网站相关
    "链接", "网址", "网站", "url", "主页", "官网",
    "给我链接", "给我网址", "发链接", "发网址",
]
_URL_PATTERN_INTENT = re.compile(r'https?://\S+')


def has_search_intent(text: str) -> bool:
    """检测用户消息是否包含搜索意图（命中则跳过 probe 直接走工具调用）"""
    lower = text.lower()
    for kw in SEARCH_INTENT_KEYWORDS:
        if kw in lower:
            return True
    if _URL_PATTERN_INTENT.search(text):
        return True
    return False


# ============================================================
# 运行时参数配置（可通过 Dashboard 动态调整）
# ============================================================

_RUNTIME_CONFIG_FILE = QQBOT_DIR / "data" / "runtime_settings.json"
SETTINGS_RELOAD_TRIGGER = QQBOT_DIR / ".reload_settings_trigger"

_RUNTIME_CONFIG_DEFAULTS = {
    "active_hours_start": 0,
    "active_hours_end": 23,
    "web_search_enabled": True,
    "stream_enabled": True,
    "stream_flush_chars": 60,
    "stream_flush_interval": 8.0,
    "stream_flush_min_chars": 80,
    "stream_max_flush_size": 300,
    "max_history_rounds": 40,
    "history_ttl_hours": 6,
    "history_save_interval": 60,
    "thinking_timer_seconds": 5,
    "multi_turn_enabled": True,
}

_runtime_config: dict = {}


def load_runtime_config():
    """从磁盘加载运行时配置，缺失项用默认值填充"""
    global _runtime_config
    _runtime_config = dict(_RUNTIME_CONFIG_DEFAULTS)
    if _RUNTIME_CONFIG_FILE.exists():
        try:
            saved = json.loads(_RUNTIME_CONFIG_FILE.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k in _RUNTIME_CONFIG_DEFAULTS:
                    try:
                        expected = type(_RUNTIME_CONFIG_DEFAULTS[k])
                        _runtime_config[k] = expected(v)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"[settings] Invalid value for '{k}': {v} ({e}), using default")
        except Exception as e:
            logger.warning(f"[settings] Failed to load runtime config: {e}")


def save_runtime_config():
    """持久化运行时配置到磁盘"""
    try:
        _RUNTIME_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _RUNTIME_CONFIG_FILE.write_text(
            json.dumps(_runtime_config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"[settings] Failed to save runtime config: {e}")


def apply_runtime_config():
    """将运行时配置同步到模块级变量（供现有代码路径直接读取）"""
    global ACTIVE_HOURS_START, ACTIVE_HOURS_END, WEB_SEARCH_ENABLED
    global STREAM_ENABLED, STREAM_FLUSH_CHARS, STREAM_FLUSH_INTERVAL, STREAM_FLUSH_MIN_CHARS, STREAM_MAX_FLUSH_SIZE
    global MAX_HISTORY_ROUNDS, MAX_HISTORY_MESSAGES
    global HISTORY_TTL, HISTORY_SAVE_INTERVAL
    global THINKING_TIMER_SECONDS, MULTI_TURN_ENABLED

    ACTIVE_HOURS_START = int(_runtime_config.get("active_hours_start", 0))
    ACTIVE_HOURS_END = int(_runtime_config.get("active_hours_end", 23))
    WEB_SEARCH_ENABLED = bool(_runtime_config.get("web_search_enabled", True))
    STREAM_ENABLED = bool(_runtime_config.get("stream_enabled", True))
    STREAM_FLUSH_CHARS = int(_runtime_config.get("stream_flush_chars", 60))
    STREAM_FLUSH_INTERVAL = float(_runtime_config.get("stream_flush_interval", 8.0))
    STREAM_FLUSH_MIN_CHARS = int(_runtime_config.get("stream_flush_min_chars", 80))
    STREAM_MAX_FLUSH_SIZE = int(_runtime_config.get("stream_max_flush_size", 300))
    MAX_HISTORY_ROUNDS = int(_runtime_config.get("max_history_rounds", 40))
    MAX_HISTORY_MESSAGES = MAX_HISTORY_ROUNDS * 2
    HISTORY_TTL = int(_runtime_config.get("history_ttl_hours", 6)) * 3600
    HISTORY_SAVE_INTERVAL = int(_runtime_config.get("history_save_interval", 60))
    THINKING_TIMER_SECONDS = int(_runtime_config.get("thinking_timer_seconds", 5))
    MULTI_TURN_ENABLED = bool(_runtime_config.get("multi_turn_enabled", True))


# 流式输出默认值（apply_runtime_config 会覆盖）
STREAM_ENABLED = True
STREAM_FLUSH_CHARS = 60
STREAM_FLUSH_INTERVAL = 8.0
STREAM_FLUSH_MIN_CHARS = 80
STREAM_SOFT_BREAKS = set("，,；;：:、\n ")  # 软断点字符
STREAM_MAX_FLUSH_SIZE = 300

# 历史相关默认值
HISTORY_TTL = 3600 * 6
HISTORY_SAVE_INTERVAL = 60
THINKING_TIMER_SECONDS = 5
MULTI_TURN_ENABLED = True

# 启动时加载并应用
load_runtime_config()
apply_runtime_config()


# ============================================================
# 聊天事件专用日志
# ============================================================

_LOG_DIR = QQBOT_DIR / "logs"
_LOG_DIR.mkdir(exist_ok=True)
chat_logger = logging.getLogger("zyw_chat.events")
chat_logger.setLevel(logging.INFO)
_fh = RotatingFileHandler(
    _LOG_DIR / "chat.log",
    maxBytes=5 * 1024 * 1024,   # 5 MB
    backupCount=3,
    encoding="utf-8",
)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%m-%d %H:%M:%S"))
chat_logger.addHandler(_fh)
chat_logger.propagate = False


# ============================================================
# 搜索健康检查
# ============================================================

_search_health = {
    "sogou": {"fail_count": 0, "last_fail": 0, "healthy": True},
    "baidu": {"fail_count": 0, "last_fail": 0, "healthy": True},
    "weibo": {"fail_count": 0, "last_fail": 0, "healthy": True},
}
_SEARCH_HEALTH_FAIL_THRESHOLD = 3
_SEARCH_HEALTH_COOLDOWN = 120


def is_search_healthy(source: str) -> bool:
    """检查搜索源是否健康可用"""
    h = _search_health.get(source)
    if not h:
        return True
    if h["healthy"]:
        return True
    if time.time() - h["last_fail"] > _SEARCH_HEALTH_COOLDOWN:
        h["healthy"] = True
        h["fail_count"] = 0
        logger.info(f"[search_health] {source} 冷却期已过，重新启用")
        return True
    return False


def update_search_health(source: str, success: bool):
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
