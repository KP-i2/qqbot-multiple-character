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
# 2026-09-10 改名: 目录 qqbot/skills → qqbot/characters
# 原因: 跟根目录的 skill/（colleague-skill，DSH 技能包）名字太像，容易混淆；
#       而这个目录下装的其实是「角色三件套」(SKILL.md + persona.md + work.md)。
# 注意: 只改了目录名和路径，代码里的 skill 概念（skill_manager / ALL_SKILLS /
#       /skills 命令 / /api/skills 接口）保持不变，见本次改动的 scope 约定。
SKILLS_DIR = QQBOT_DIR / "characters"
CORPUS_DIR = QQBOT_DIR.parent / "corpus"
# 角色级语料别名：某个角色没有自己的语料目录 / corpus_ref 时，借助另一个角色的语料。
# 生效条件：该角色既没有 corpus/{name}_* 目录、也没有 characters/{name}/corpus_ref/；
# 一旦角色自己有了语料，别名自动失效（优先用自己的）。
# 只影响检索；Dashboard 的蒸馏管线仍要求角色自有语料，不吃这个别名。
# 别名映射属于本地私有配置，放在 private/corpus_aliases.json（不进公开仓库），格式：
#   {"<角色目录名>": "<被借用语料的角色名/前缀>"}
CORPUS_ALIASES: dict[str, str] = {}
_CORPUS_ALIAS_FILE = QQBOT_DIR.parent / "private" / "corpus_aliases.json"
try:
    if _CORPUS_ALIAS_FILE.exists():
        _aliases = json.loads(_CORPUS_ALIAS_FILE.read_text(encoding="utf-8"))
        if isinstance(_aliases, dict):
            CORPUS_ALIASES = {str(k): str(v) for k, v in _aliases.items()}
except Exception:
    # 配置坏了不影响启动：退化成"没有别名"
    CORPUS_ALIASES = {}
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

# minimax Provider 注册（OpenAI 兼容协议）
# 2026-07-23: 主对话已切到 deepseek, minimax 仅作为可选的多模态 vision provider (image-to-text)
# 注意: 该 key 是 minimax 中转,非个人 MiniMax 直连 key
_minimax_enabled_raw = getattr(config, "minimax_enabled", "false")
MINIMAX_ENABLED: bool = str(_minimax_enabled_raw).lower() == "true"
MINIMAX_API_KEY: str = getattr(config, "minimax_api_key", "")
MINIMAX_BASE_URL: str = getattr(config, "minimax_base_url", "https://api.minimaxi.com/v1")
MINIMAX_MODEL: str = getattr(config, "minimax_model", "MiniMax-M3")

# SiliconFlow (硅基流动) Provider — OpenAI 兼容协议
# 2026-07-23: minimax API 到期, 改用硅基流动 Qwen3.5-9B 做 vision (读图转文字)
# 字段全用小写, 跟 .env 里现有的 siliconflow_api_key 保持一致
_siliconflow_enabled_raw = getattr(config, "siliconflow_enabled", "false")
SILICONFLOW_ENABLED: bool = str(_siliconflow_enabled_raw).lower() == "true"
SILICONFLOW_API_KEY: str = getattr(config, "siliconflow_api_key", "")
SILICONFLOW_BASE_URL: str = getattr(config, "siliconflow_base_url", "https://api.siliconflow.cn/v1")
SILICONFLOW_MODEL: str = getattr(config, "siliconflow_model", "Qwen/Qwen3.5-9B")

# DeepSeek 官方 Vision Provider — OpenAI 兼容协议（2026-08-30 接入）
# 模型: deepseek-v4-flash-vision-exp（官方多模态实验模型，服务端实际路由到 deepseek-flash）
# 复用 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL（同平台同 key，无需新增凭证）
#
# 实测结论（2026-08-30，用 emoji/*.png 实测）:
#   1) base64 data URI 传图稳定可用；远程 URL 让服务端抓取**不可靠**
#      （python.org 成功，upload.wikimedia.org 直接 400 "Failed to download image"）
#      → 所以走 deepseek vision 时一律先转 base64，详见 provider.describe_image
#   2) 必须显式 thinking={"type":"disabled"}：该模型默认开思考，
#      max_tokens 会被 reasoning 吃光导致 content 为空。
#      同一张图关闭思考后 253 tokens vs 开启 725 tokens，且描述反而更详细。
#   3) enable_thinking 参数对本模型无效（那是硅基流动的字段），传了照样思考。
_deepseek_vision_enabled_raw = (
    getattr(config, "deepseek_vision_enabled", "")
    or os.getenv("DEEPSEEK_VISION_ENABLED", "")
    or "false"
)
DEEPSEEK_VISION_ENABLED: bool = str(_deepseek_vision_enabled_raw).lower() == "true"
DEEPSEEK_VISION_MODEL: str = (
    getattr(config, "deepseek_vision_model", "")
    or os.getenv("DEEPSEEK_VISION_MODEL", "")
    or "deepseek-v4-flash-vision-exp"
)


# ============================================================
# 通用参数
# ============================================================

# 活跃时间开关
ACTIVE_HOURS_START: int = int(getattr(config, "active_hours_start", 0))
ACTIVE_HOURS_END: int = int(getattr(config, "active_hours_end", 23))

# Web Search 功能
_web_search_raw = getattr(config, "web_search_enabled", "true")
WEB_SEARCH_ENABLED: bool = str(_web_search_raw).lower() == "true" if _web_search_raw is not None else True

# ── 联网搜索后端：2026-10-30 起只走 DeepSeek 官方 Search API ──
# 文档: https://api-docs.deepseek.com/zh-cn/guides/search/
# 计费: 每 1000 次成功请求 5 元；失败/无结果不计费
_ds_search_enabled_raw = (
    getattr(config, "deepseek_search_enabled", "")
    or os.getenv("DEEPSEEK_SEARCH_ENABLED", "")
    or "true"
)
DEEPSEEK_SEARCH_ENABLED: bool = str(_ds_search_enabled_raw).lower() == "true"
DEEPSEEK_SEARCH_COUNT: int = int(
    getattr(config, "deepseek_search_count", "") or os.getenv("DEEPSEEK_SEARCH_COUNT", "") or 5
)
DEEPSEEK_SEARCH_TIMEOUT: float = float(
    getattr(config, "deepseek_search_timeout", "") or os.getenv("DEEPSEEK_SEARCH_TIMEOUT", "") or 10
)
DEEPSEEK_SEARCH_SUMMARY_MAX: int = int(
    getattr(config, "deepseek_search_summary_max", "")
    or os.getenv("DEEPSEEK_SEARCH_SUMMARY_MAX", "")
    or 600
)

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
    # 2026-07-23 补: 高频但漏掉的"想知道外部信息"信号词
    # 之前 "kimi k3有什么新闻" 被判成非搜索意图, deepseek 凭知识瞎答
    "新闻", "新消息", "动态", "近况", "更新", "进展", "资讯", "报道", "消息",
    # 2026-09-10 补: 热搜/热点类提问（配合 search.py 里的微博热搜兜底）
    "热搜", "热点", "热门", "话题榜", "都在聊",
    # 追问 / 跨人 / 跨话题
    "她呢", "他呢", "那她", "那他", "那小", "那大",
    "其他人", "别人呢", "队友呢", "队员呢",
    "几月", "几号", "多大", "多高", "哪里人",
    "关于", "说说", "讲讲", "网上", "有介绍",
    # 链接/网站相关
    "链接", "网址", "网站", "url", "主页", "官网",
    "给我链接", "给我网址", "发链接", "发网址",
    # 2026-09-13 补: 演出/日程类提问。
    # 起因: "你们这周在哪演出" 没命中任何关键词 → probe 判无需工具 → 模型编了一个场馆和时间。
    # 这类问题的答案（本周在哪演、几点的场）**一定**不在角色设定里，必须查。
    "演出", "公演", "oneman", "生诞", "特典会", "livehouse",
    "日程", "行程", "排期", "档期", "场次",
    "这周", "本周", "下周", "周末", "哪天", "几号", "什么时候",
    "场地", "场馆", "门票", "票价", "开票", "抢票",
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
    # 2026-07-23: 识图开关 — minimax vision API 不可用时设 false 关闭 vision 调用
    "image_handler_enabled": True,
    # 通用 LLM 运行时参数（适用于 active provider: deepseek 当前主选）
    # 2026-07-23 重命名: 之前叫 minimax_*, 实际是给 active provider 用的通用参数
    "llm_temperature": 0.95,
    "llm_top_p": 0.9,
    "llm_max_tokens": 16384,
    "llm_reasoning_effort_with_tools": "high",
    "llm_reasoning_effort_no_tools": "high",
    # 当前激活的 LLM provider（"minimax" 或 "deepseek"），两个 provider 不互相回退
    # 2026-07-23: 默认从 minimax 切到 deepseek
    "active_llm_provider": "deepseek",
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
    global IMAGE_HANDLER_ENABLED  # 2026-07-23 新增: 关闭 vision 的开关
    # 通用 LLM 运行时参数（2026-07-23 重命名: 之前叫 MINIMAX_*）
    global LLM_TEMPERATURE, LLM_TOP_P, LLM_MAX_TOKENS
    global LLM_REASONING_EFFORT_WITH_TOOLS, LLM_REASONING_EFFORT_NO_TOOLS
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
    # 2026-07-23: 识图开关 (minimax vision 关闭用)
    IMAGE_HANDLER_ENABLED = bool(_runtime_config.get("image_handler_enabled", True))
    MULTI_TURN_ENABLED = bool(_runtime_config.get("multi_turn_enabled", True))
    TEST_MODE_ENABLED = bool(_runtime_config.get("test_mode_enabled", False))

    # 通用 LLM 运行时参数（适用于 active provider, dashboard 可热改）
    LLM_TEMPERATURE = float(_runtime_config.get("llm_temperature", 0.95))
    LLM_TOP_P = float(_runtime_config.get("llm_top_p", 0.9))
    LLM_MAX_TOKENS = int(_runtime_config.get("llm_max_tokens", 16384))
    LLM_REASONING_EFFORT_WITH_TOOLS = str(
        _runtime_config.get("llm_reasoning_effort_with_tools", "high")
    )
    LLM_REASONING_EFFORT_NO_TOOLS = str(
        _runtime_config.get("llm_reasoning_effort_no_tools", "high")
    )

    # 当前激活的 LLM provider（dashboard 切换）
    ACTIVE_LLM_PROVIDER = str(_runtime_config.get("active_llm_provider", "deepseek"))
    if ACTIVE_LLM_PROVIDER not in ("minimax", "deepseek"):
        ACTIVE_LLM_PROVIDER = "deepseek"


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

# 通用 LLM 运行时参数默认值（apply_runtime_config 会覆盖）
LLM_TEMPERATURE = 0.95
LLM_TOP_P = 0.9
LLM_MAX_TOKENS = 16384
LLM_REASONING_EFFORT_WITH_TOOLS = "high"
LLM_REASONING_EFFORT_NO_TOOLS = "high"
ACTIVE_LLM_PROVIDER = "deepseek"

# 启动时加载并应用
load_runtime_config()
apply_runtime_config()


# ============================================================
# 聊天事件专用日志
# ============================================================

_LOG_DIR = QQBOT_DIR / "logs"
_LOG_DIR.mkdir(exist_ok=True)
chat_logger = logging.getLogger("char_chat.events")
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

# 2026-09-12: 只保留真正在用的引擎 —— sogou/weibo/ddg 已在 search.py 里删除
# （反爬 / 需登录态 / 不可达），留着它们只会让 search_health.json 反复写回死条目。
_search_health: dict[str, dict] = {
    # 2026-10-30: 联网搜索只走 DeepSeek 官方 Search API
    "deepseek_search": {"fail_count": 0, "last_fail": 0, "healthy": True},
}
_SEARCH_HEALTH_FAIL_THRESHOLD = 3
_SEARCH_HEALTH_COOLDOWN = 120
# 2026-08-30: 写盘限频 — 搜索源连续失败时不再每次 update 都写磁盘
_SEARCH_HEALTH_SAVE_INTERVAL = 5.0
_last_search_health_save = 0.0


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
    """持久化搜索健康状态到磁盘（5s 限频，避免高频失败时写盘放大）"""
    global _last_search_health_save
    now = time.time()
    if now - _last_search_health_save < _SEARCH_HEALTH_SAVE_INTERVAL:
        return
    _last_search_health_save = now
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
