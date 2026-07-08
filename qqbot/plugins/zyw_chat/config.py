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

# minimax API（OpenAI 兼容协议；启用时为新的主选，失败回退 DeepSeek）
# 注意: 该 key 是 minimax 中转,非个人 MiniMax 直连 key
_minimax_enabled_raw = getattr(config, "minimax_enabled", "false")
MINIMAX_ENABLED: bool = str(_minimax_enabled_raw).lower() == "true"
MINIMAX_API_KEY: str = getattr(config, "minimax_api_key", "")
MINIMAX_BASE_URL: str = getattr(config, "minimax_base_url", "https://api.minimaxi.com/v1")
MINIMAX_MODEL: str = getattr(config, "minimax_model", "MiniMax-M3")
# 备选高速模型（成本更低、速度更快，适合高频/搜索场景）
MINIMAX_FAST_MODEL: str = getattr(config, "minimax_fast_model", "MiniMax-M2.7-highspeed")
# 是否启用高速模型（探测/低延迟场景），默认 False 保持主选标准版
MINIMAX_USE_HIGH_SPEED: bool = str(
    getattr(config, "minimax_use_high_speed", "false") or "false"
).lower() == "true"


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
ADMIN_QQ = getattr(config, "admin_qq", "") or os.getenv("ADMIN_QQ", "")
DEFAULT_SKILL_NAME_CONFIG = str(getattr(config, "default_skill", "") or os.getenv("DEFAULT_SKILL", "") or "")

# 白名单群自动回复配置（无需 @，按概率回复）
# NoneBot2 pydantic 对 custom fields 不会自动从 env 读，fallback 到 os.environ
AUTO_REPLY_GROUP_IDS_RAW: str = str(
    getattr(config, "auto_reply_group_ids", "") or os.getenv("AUTO_REPLY_GROUP_IDS", "") or ""
)
AUTO_REPLY_GROUP_PROBABILITY: float = float(
    getattr(config, "auto_reply_group_probability", 0.0)
    or os.getenv("AUTO_REPLY_GROUP_PROBABILITY", 0.0)
    or 0.0
)


def _parse_qq_set(raw: str) -> set[str]:
    """解析逗号分隔的 QQ 号字符串 → set（去重、去空白、支持中英文逗号）"""
    if not raw:
        return set()
    return {x.strip() for x in str(raw).replace("，", ",").split(",") if x.strip()}


ADMIN_QQ_SET: set[str] = _parse_qq_set(ADMIN_QQ)
AUTO_REPLY_GROUP_IDS_SET: set[str] = _parse_qq_set(AUTO_REPLY_GROUP_IDS_RAW)


def is_admin(uid: str | int) -> bool:
    """检查 uid 是否为管理员（支持多管理员，逗号或中文逗号分隔）"""
    if not ADMIN_QQ_SET:
        return False  # 未配置管理员时拒绝所有（防误操作）
    return str(uid) in ADMIN_QQ_SET


def is_auto_reply_group(group_id: str | int) -> bool:
    """检查群是否在自动回复白名单"""
    return str(group_id) in AUTO_REPLY_GROUP_IDS_SET


def should_auto_reply_now() -> bool:
    """白名单群内是否本次触发自动回复（按概率）"""
    if AUTO_REPLY_GROUP_PROBABILITY <= 0:
        return False
    if AUTO_REPLY_GROUP_PROBABILITY >= 1:
        return True
    import random
    return random.random() < AUTO_REPLY_GROUP_PROBABILITY


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
    "stream_time_grace_seconds": 1.5,
    "stream_max_flush_size": 300,
    "max_history_rounds": 100,
    "history_ttl_hours": 36,
    "history_save_interval": 60,
    "thinking_timer_seconds": 120,
    "multi_turn_enabled": True,
    "test_mode_enabled": True,
    # minimax 专属运行时参数
    "minimax_temperature": 1.2,
    "minimax_top_p": 0.95,
    "minimax_max_tokens": 16384,
    "minimax_use_high_speed": False,
    "minimax_reasoning_effort_with_tools": "high",
    "minimax_reasoning_effort_no_tools": "high",
    # 当前激活的 LLM provider（"minimax" 或 "deepseek"），两个 provider 不互相回退
    "active_llm_provider": "minimax",
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
    global STREAM_ENABLED, STREAM_FLUSH_CHARS, STREAM_FLUSH_INTERVAL, STREAM_FLUSH_MIN_CHARS, STREAM_TIME_GRACE_SECONDS, STREAM_MAX_FLUSH_SIZE
    global MAX_HISTORY_ROUNDS, MAX_HISTORY_MESSAGES
    global HISTORY_TTL, HISTORY_SAVE_INTERVAL
    global THINKING_TIMER_SECONDS, MULTI_TURN_ENABLED, TEST_MODE_ENABLED
    # minimax runtime params
    global MINIMAX_TEMPERATURE, MINIMAX_TOP_P, MINIMAX_MAX_TOKENS
    global MINIMAX_USE_HIGH_SPEED
    global MINIMAX_REASONING_EFFORT_WITH_TOOLS, MINIMAX_REASONING_EFFORT_NO_TOOLS
    global ACTIVE_LLM_PROVIDER

    ACTIVE_HOURS_START = int(_runtime_config.get("active_hours_start", 0))
    ACTIVE_HOURS_END = int(_runtime_config.get("active_hours_end", 23))
    WEB_SEARCH_ENABLED = bool(_runtime_config.get("web_search_enabled", True))
    STREAM_ENABLED = bool(_runtime_config.get("stream_enabled", True))
    STREAM_FLUSH_CHARS = int(_runtime_config.get("stream_flush_chars", 60))
    STREAM_FLUSH_INTERVAL = float(_runtime_config.get("stream_flush_interval", 8.0))
    STREAM_FLUSH_MIN_CHARS = int(_runtime_config.get("stream_flush_min_chars", 80))
    STREAM_TIME_GRACE_SECONDS = float(_runtime_config.get("stream_time_grace_seconds", 1.5))
    STREAM_MAX_FLUSH_SIZE = int(_runtime_config.get("stream_max_flush_size", 300))
    MAX_HISTORY_ROUNDS = int(_runtime_config.get("max_history_rounds", 40))
    MAX_HISTORY_MESSAGES = MAX_HISTORY_ROUNDS * 2
    HISTORY_TTL = int(_runtime_config.get("history_ttl_hours", 6)) * 3600
    HISTORY_SAVE_INTERVAL = int(_runtime_config.get("history_save_interval", 60))
    THINKING_TIMER_SECONDS = int(_runtime_config.get("thinking_timer_seconds", 5))
    MULTI_TURN_ENABLED = bool(_runtime_config.get("multi_turn_enabled", True))
    TEST_MODE_ENABLED = bool(_runtime_config.get("test_mode_enabled", False))

    # minimax 运行时参数（默认跟 .env 一致；Dashboard 可热改）
    MINIMAX_TEMPERATURE = float(_runtime_config.get("minimax_temperature", 1.2))
    MINIMAX_TOP_P = float(_runtime_config.get("minimax_top_p", 0.95))
    MINIMAX_MAX_TOKENS = int(_runtime_config.get("minimax_max_tokens", 16384))
    MINIMAX_USE_HIGH_SPEED = bool(_runtime_config.get("minimax_use_high_speed", False))
    MINIMAX_REASONING_EFFORT_WITH_TOOLS = str(
        _runtime_config.get("minimax_reasoning_effort_with_tools", "high")
    )
    MINIMAX_REASONING_EFFORT_NO_TOOLS = str(
        _runtime_config.get("minimax_reasoning_effort_no_tools", "high")
    )

    # 当前激活的 LLM provider（dashboard 切换）
    ACTIVE_LLM_PROVIDER = str(_runtime_config.get("active_llm_provider", "minimax"))
    if ACTIVE_LLM_PROVIDER not in ("minimax", "deepseek"):
        ACTIVE_LLM_PROVIDER = "minimax"


# 流式输出默认值（apply_runtime_config 会覆盖）
STREAM_ENABLED = True
STREAM_FLUSH_CHARS = 60
STREAM_FLUSH_INTERVAL = 8.0
STREAM_FLUSH_MIN_CHARS = 80
STREAM_TIME_GRACE_SECONDS = 1.5
STREAM_MAX_FLUSH_SIZE = 300

# 历史相关默认值
HISTORY_TTL = 3600 * 6
HISTORY_SAVE_INTERVAL = 60
THINKING_TIMER_SECONDS = 5
MULTI_TURN_ENABLED = True
TEST_MODE_ENABLED = True

# minimax 运行时参数默认值（apply_runtime_config 会覆盖）
MINIMAX_TEMPERATURE = 1.2
MINIMAX_TOP_P = 0.95
MINIMAX_MAX_TOKENS = 16384
MINIMAX_USE_HIGH_SPEED = False
MINIMAX_REASONING_EFFORT_WITH_TOOLS = "high"
MINIMAX_REASONING_EFFORT_NO_TOOLS = "high"
ACTIVE_LLM_PROVIDER = "minimax"

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
# 搜索健康检查（持久化到磁盘，重启后恢复）
# ============================================================

_SEARCH_HEALTH_FILE = QQBOT_DIR / "data" / "search_health.json"

_search_health: dict[str, dict] = {
    "sogou": {"fail_count": 0, "last_fail": 0, "healthy": True},
    "baidu": {"fail_count": 0, "last_fail": 0, "healthy": True},
    "weibo": {"fail_count": 0, "last_fail": 0, "healthy": True},
    "ddg":   {"fail_count": 0, "last_fail": 0, "healthy": True},
}
_SEARCH_HEALTH_FAIL_THRESHOLD = 3
_SEARCH_HEALTH_COOLDOWN = 120


def _load_search_health():
    """从磁盘恢复搜索健康状态（如果存在）"""
    global _search_health
    if not _SEARCH_HEALTH_FILE.exists():
        return
    try:
        saved = json.loads(_SEARCH_HEALTH_FILE.read_text(encoding="utf-8"))
        for src, h in saved.items():
            if src in _search_health and isinstance(h, dict):
                _search_health[src].update({
                    "fail_count": int(h.get("fail_count", 0)),
                    "last_fail": float(h.get("last_fail", 0)),
                    "healthy": bool(h.get("healthy", True)),
                })
        logger.info(f"[search_health] 从磁盘恢复: { {k: v['healthy'] for k, v in _search_health.items()} }")
    except Exception as e:
        logger.warning(f"[search_health] 恢复失败: {e}")


def _save_search_health():
    """持久化搜索健康状态到磁盘"""
    try:
        _SEARCH_HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SEARCH_HEALTH_FILE.write_text(
            json.dumps(_search_health, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.debug(f"[search_health] 保存失败: {e}")


_load_search_health()


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


    _save_search_health()


# ============================================================
# 搜索/语料库 metrics（内存累计，bot 重启清零，Dashboard 可读）
# ============================================================

_metrics: dict[str, dict] = {
    "web_search": {"calls": 0, "hits": 0, "fallback_rounds": 0, "ddg_hits": 0},
    "search_corpus": {"calls": 0, "hits": 0, "fuzzy_hits": 0, "zero_results": 0},
    "by_source": {"baidu": 0, "weibo": 0, "sogou": 0, "ddg": 0},
}


def record_metric(category: str, key: str = "calls", n: int = 1):
    """记录一次调用/命中。

    category: web_search | search_corpus | by_source
    key: calls / hits / fallback_rounds / fuzzy_hits / zero_results / 搜索源名(baidu/weibo/sogou/ddg)
    """
    bucket = _metrics.get(category)
    if not bucket:
        return
    bucket[key] = bucket.get(key, 0) + n


def get_metrics() -> dict:
    """Dashboard 读取当前 metrics 快照"""
    import copy
    return copy.deepcopy(_metrics)


def reset_metrics():
    """Dashboard 手动重置"""
    for cat in _metrics.values():
        cat.clear()
