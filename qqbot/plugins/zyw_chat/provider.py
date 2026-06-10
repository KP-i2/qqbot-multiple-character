"""
LLM Provider 管理（OpenAI / DeepSeek 回退）
"""

import time

from nonebot import logger

from . import config as cfg


# Provider 健康状态（primary=openai, fallback=deepseek）
_provider_health: dict[str, dict] = {
    "openai": {"healthy": True, "fail_count": 0, "last_fail": 0},
    "deepseek": {"healthy": True, "fail_count": 0, "last_fail": 0},
}
_PROVIDER_COOLDOWN = 120  # 标记不健康后的冷却秒数


def get_provider(name: str) -> dict:
    """获取指定 provider 的配置信息"""
    if name == "openai":
        return {"name": "openai", "base_url": cfg.OPENAI_BASE_URL, "api_key": cfg.OPENAI_API_KEY, "model": cfg.OPENAI_MODEL}
    return {"name": "deepseek", "base_url": cfg.DEEPSEEK_BASE_URL, "api_key": cfg.DEEPSEEK_API_KEY, "model": cfg.DEEPSEEK_MODEL}


def get_active_provider() -> dict:
    """返回当前可用的主 provider（优先 OpenAI，回退 DeepSeek）"""
    now = time.time()
    if cfg.OPENAI_ENABLED and cfg.OPENAI_API_KEY and cfg.OPENAI_BASE_URL:
        h = _provider_health["openai"]
        if h["healthy"] or (now - h["last_fail"] > _PROVIDER_COOLDOWN):
            h["healthy"] = True
            return get_provider("openai")
    return get_provider("deepseek")


def mark_provider_failed(name: str):
    """标记 provider 失败，连续失败则进入冷却"""
    h = _provider_health.get(name)
    if not h:
        return
    h["fail_count"] += 1
    h["last_fail"] = time.time()
    if h["fail_count"] >= 2:
        h["healthy"] = False
        logger.warning(f"Provider '{name}' 连续失败 {h['fail_count']} 次，进入 {_PROVIDER_COOLDOWN}s 冷却")


def mark_provider_healthy(name: str):
    """标记 provider 恢复健康"""
    h = _provider_health.get(name)
    if h:
        h["healthy"] = True
        h["fail_count"] = 0


def adapt_payload_for_provider(payload: dict, provider_name: str) -> dict:
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
