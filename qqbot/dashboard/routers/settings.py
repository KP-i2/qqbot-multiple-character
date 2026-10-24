"""
参数设置：运行时参数读写 + LLM Provider 状态
（2026-08-30 拆分自 main.py；llm-providers 增加 mtime 缓存）
"""
import json
import time
from pathlib import Path

from fastapi import APIRouter, Request

from ._shared import PRIVATE_DIR, QQBOT_DIR, logger

router = APIRouter()

_RUNTIME_SETTINGS_FILE = QQBOT_DIR / "data" / "runtime_settings.json"
_SETTINGS_RELOAD_TRIGGER = QQBOT_DIR / ".reload_settings_trigger"

_SETTINGS_SCHEMA = {
    "active_hours_start":    {"type": "int",   "label": "活跃时段起始 (0-23)",    "default": 0,    "category": "bot"},
    "active_hours_end":      {"type": "int",   "label": "活跃时段结束 (0-23)",    "default": 23,   "category": "bot"},
    "web_search_enabled":    {"type": "bool",  "label": "联网搜索",                "default": True,  "category": "bot"},
    "stream_enabled":        {"type": "bool",  "label": "流式输出",                "default": True,  "category": "stream"},
    "stream_flush_chars":    {"type": "int",   "label": "断句字符阈值",            "default": 60,    "category": "stream", "min": 20, "max": 300},
    "stream_flush_interval": {"type": "float", "label": "断句等待秒数",            "default": 8.0,   "category": "stream", "min": 1, "max": 15},
    "stream_flush_min_chars":{"type": "int",   "label": "最小累积字符数",          "default": 80,    "category": "stream", "min": 20, "max": 300},
    "stream_max_flush_size": {"type": "int",   "label": "单段最大字符",            "default": 180,   "category": "stream", "min": 50, "max": 1000},
    "max_history_rounds":    {"type": "int",   "label": "最大对话轮数",            "default": 100,   "category": "history", "min": 1, "max": 200},
    "history_ttl_hours":     {"type": "int",   "label": "历史过期时间 (小时)",     "default": 36,    "category": "history", "min": 1, "max": 168},
    "history_save_interval": {"type": "int",   "label": "保存间隔 (秒)",           "default": 60,    "category": "history", "min": 10, "max": 600},
    "thinking_timer_seconds":{"type": "int",   "label": "等待提示秒数",            "default": 120,   "category": "timing", "min": 1, "max": 300},
    # 2026-07-23: 识图总开关 (vision 不可用时关掉 — 走 deepseek 会触发 image_url 400)
    "image_handler_enabled": {"type": "bool",  "label": "识图 (vision, 关闭后图片消息走降级占位)",  "default": True, "category": "llm"},
    "multi_turn_enabled":    {"type": "bool",  "label": "多轮对话",                "default": True,  "category": "bot"},
    "test_mode_enabled":     {"type": "bool",  "label": "测试模式 (仅管理员)",     "default": True,  "category": "bot"},
    "active_llm_provider":   {"type": "select","label": "主对话 Provider (含图消息走 vision,其它都走这个)",  "default": "deepseek",
                              "options": ["minimax", "deepseek"],
                              "category": "llm"},
    # ── 通用 LLM 参数（2026-07-23 重命名: 之前叫 minimax_*, 实际是 active provider 通用参数）
    "llm_max_tokens":                  {"type": "int",  "label": "Max Tokens",                    "default": 16384, "category": "llm", "min": 100,  "max": 32000},
    "llm_temperature":                 {"type": "float","label": "Temperature",                  "default": 0.95,  "category": "llm", "min": 0.0,  "max": 2.0},
    "llm_top_p":                       {"type": "float","label": "Top P",                        "default": 0.9,   "category": "llm", "min": 0.0,  "max": 1.0},
    "llm_reasoning_effort_with_tools": {"type": "select","label": "思考强度(带工具)",            "default": "high",
                                        "options": ["low", "medium", "high"], "category": "llm"},
    "llm_reasoning_effort_no_tools":   {"type": "select","label": "思考强度(无工具)",            "default": "high",
                                        "options": ["low", "medium", "high"], "category": "llm"},
}


@router.get("/api/settings")
async def api_get_settings():
    """读取当前运行时参数"""
    current = {}
    if _RUNTIME_SETTINGS_FILE.exists():
        try:
            current = json.loads(_RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 合并：以 schema 为准，已保存的值覆盖默认值
    result = {}
    for key, meta in _SETTINGS_SCHEMA.items():
        result[key] = {
            "value": current.get(key, meta["default"]),
            "type": meta["type"],
            "label": meta["label"],
            "category": meta["category"],
            "default": meta["default"],
        }
        if "min" in meta:
            result[key]["min"] = meta["min"]
        if "max" in meta:
            result[key]["max"] = meta["max"]
    return {"ok": True, "settings": result}


# ── LLM Provider 双轨状态（2026-06-28 重构；2026-08-30 增加 mtime 缓存）──
_qqbot_env = QQBOT_DIR / ".env"
_private_env = PRIVATE_DIR / ".env"


def _config_files_mtime() -> float:
    """取 .env + runtime_settings.json 中最新的修改时间，用于缓存失效判断"""
    latest = 0.0
    for p in (_private_env, _qqbot_env, _RUNTIME_SETTINGS_FILE):
        try:
            latest = max(latest, p.stat().st_mtime)
        except OSError:
            pass
    return latest


def _read_env_vars() -> dict:
    """解析 .env 文件（优先 private/.env，其次 qqbot/.env）"""
    env_vars: dict = {}
    for env_path in (_private_env, _qqbot_env):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env_vars[k.strip()] = v.strip()
        break  # 第一个存在的用完即止
    return env_vars


def _build_llm_providers() -> dict:
    """读取 .env + runtime_settings，返回双 provider 状态（同步，走 executor）"""
    env_vars = _read_env_vars()
    runtime: dict = {}
    if _RUNTIME_SETTINGS_FILE.exists():
        try:
            runtime = json.loads(_RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    class zyw_cfg:  # 替身,只在 dashboard 用
        ACTIVE_LLM_PROVIDER = runtime.get("active_llm_provider", "deepseek")
        IMAGE_HANDLER_ENABLED = bool(runtime.get("image_handler_enabled", True))
        MINIMAX_ENABLED = env_vars.get("MINIMAX_ENABLED", "false").lower() == "true"
        MINIMAX_API_KEY = env_vars.get("MINIMAX_API_KEY", "")
        MINIMAX_BASE_URL = env_vars.get("MINIMAX_BASE_URL", "")
        MINIMAX_MODEL = env_vars.get("MINIMAX_MODEL", "MiniMax-M3")
        DEEPSEEK_API_KEY = env_vars.get("DEEPSEEK_API_KEY", "")
        DEEPSEEK_MODEL = env_vars.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
        # 2026-08-30 新增: DeepSeek 官方 vision（复用 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL）
        DEEPSEEK_VISION_ENABLED = env_vars.get("DEEPSEEK_VISION_ENABLED", "false").lower() == "true"
        DEEPSEEK_VISION_MODEL = env_vars.get("DEEPSEEK_VISION_MODEL", "deepseek-v4-flash-vision-exp")
        # 2026-07-23 新增: 硅基流动字段 (用 .env 里的小写 key,跟现有命名一致)
        SILICONFLOW_ENABLED = env_vars.get("siliconflow_enabled", "false").lower() == "true"
        SILICONFLOW_API_KEY = env_vars.get("siliconflow_api_key", "")
        SILICONFLOW_BASE_URL = env_vars.get("siliconflow_base_url", "")
        SILICONFLOW_MODEL = env_vars.get("siliconflow_model", "Qwen/Qwen3.5-9B")

    active = getattr(zyw_cfg, "ACTIVE_LLM_PROVIDER", "deepseek")
    if active == "minimax":
        chat_available = bool(zyw_cfg.MINIMAX_ENABLED and zyw_cfg.MINIMAX_API_KEY
                              and not zyw_cfg.MINIMAX_API_KEY.startswith("sk-your"))
        chat_model = zyw_cfg.MINIMAX_MODEL
    else:
        chat_available = bool(zyw_cfg.DEEPSEEK_API_KEY
                              and not zyw_cfg.DEEPSEEK_API_KEY.startswith("sk-your"))
        chat_model = zyw_cfg.DEEPSEEK_MODEL

    # 2026-07-23: vision 状态 = 开关 + provider 可用性 双重决定
    # 2026-08-30: 优先级链改为 deepseek-vision > siliconflow > minimax，
    # 必须跟 provider.get_vision_provider() 完全一致，否则面板显示与实际调用不符。
    if (zyw_cfg.DEEPSEEK_VISION_ENABLED and zyw_cfg.DEEPSEEK_API_KEY
            and not zyw_cfg.DEEPSEEK_API_KEY.startswith("sk-your")):
        vision_provider_ok = True
        vision_name = "deepseek-vision"
        vision_model = zyw_cfg.DEEPSEEK_VISION_MODEL
    elif zyw_cfg.SILICONFLOW_ENABLED and zyw_cfg.SILICONFLOW_API_KEY and zyw_cfg.SILICONFLOW_BASE_URL:
        vision_provider_ok = True
        vision_name = "siliconflow"
        vision_model = zyw_cfg.SILICONFLOW_MODEL
    elif (zyw_cfg.MINIMAX_ENABLED and zyw_cfg.MINIMAX_API_KEY
          and zyw_cfg.MINIMAX_BASE_URL
          and not zyw_cfg.MINIMAX_API_KEY.startswith("sk-your")
          and not zyw_cfg.MINIMAX_API_KEY.startswith("sk-cp-your")):
        vision_provider_ok = True
        vision_name = "minimax"
        vision_model = zyw_cfg.MINIMAX_MODEL
    else:
        vision_provider_ok = False
        vision_name = None
        vision_model = None
    vision_enabled = bool(zyw_cfg.IMAGE_HANDLER_ENABLED)
    vision_available = vision_enabled and vision_provider_ok
    if not vision_available and vision_enabled:
        vision_model = (
            zyw_cfg.DEEPSEEK_VISION_MODEL if zyw_cfg.DEEPSEEK_API_KEY
            else (zyw_cfg.SILICONFLOW_MODEL if zyw_cfg.SILICONFLOW_API_KEY
                  else (zyw_cfg.MINIMAX_MODEL if zyw_cfg.MINIMAX_API_KEY else None))
        )
    if not vision_enabled:
        vision_warning = "识图已关闭 (image_handler_enabled=False)"
    elif not vision_provider_ok:
        vision_warning = "vision provider 不可用,含图消息会触发 deepseek image_url 400"
    else:
        vision_warning = None

    return {
        "ok": True,
        "routing_strategy": "vision_then_chat",
        "description": (
            f"含图消息: {vision_name or 'vision provider'} 转文字描述 -> deepseek 处理对话\n"
            "无图消息: 直接 deepseek 处理\n"
            "vision provider 永远只做图片描述,不接触对话上下文"
        ),
        "chat_provider": {
            "name": active,
            "model": chat_model,
            "available": chat_available,
            "role": "主对话 / 工具 / 流式 / 用户画像",
        },
        "vision_provider": {
            "name": vision_name or "none",
            "model": vision_model,
            "available": vision_available,
            "enabled": vision_enabled,  # 2026-07-23 新增: 区分"用户主动关"和"provider 不可用"
            "role": "图片描述 (只读图,不参与对话)",
            "warning": vision_warning,
        },
    }


# mtime 缓存：配置没改就不重复解析 .env
_llm_cache = {"mtime": None, "payload": None}


@router.get("/api/llm-providers")
async def api_llm_providers():
    """返回主对话 + 图片描述 两个 provider 的实时状态（前端 30s 轮询，带 mtime 缓存）"""
    import asyncio
    mtime = _config_files_mtime()
    if _llm_cache["mtime"] == mtime and _llm_cache["payload"] is not None:
        return _llm_cache["payload"]
    payload = await asyncio.get_event_loop().run_in_executor(None, _build_llm_providers)
    _llm_cache["mtime"] = mtime
    _llm_cache["payload"] = payload
    return payload


@router.put("/api/settings")
async def api_update_settings(request: Request):
    """更新运行时参数并触发 bot 热加载"""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "msg": "无效的 JSON 请求"}
    if not isinstance(body, dict):
        return {"ok": False, "msg": "请求体必须为 JSON 对象"}
    # 校验 + 类型转换
    validated = {}
    for key, raw_val in body.items():
        if key not in _SETTINGS_SCHEMA:
            continue
        meta = _SETTINGS_SCHEMA[key]
        try:
            if meta["type"] == "int":
                val = int(raw_val)
                if "min" in meta:
                    val = max(meta["min"], val)
                if "max" in meta:
                    val = min(meta["max"], val)
            elif meta["type"] == "float":
                val = float(raw_val)
                if "min" in meta:
                    val = max(meta["min"], val)
                if "max" in meta:
                    val = min(meta["max"], val)
            elif meta["type"] == "bool":
                val = bool(raw_val) if not isinstance(raw_val, str) else raw_val.lower() in ("true", "1", "yes")
            else:
                val = raw_val
            validated[key] = val
        except (ValueError, TypeError) as e:
            return {"ok": False, "msg": f"参数 '{key}' 类型错误: {e}"}
    # 写入配置文件（先读旧值合并，避免丢失前端未发送的 key）
    try:
        _RUNTIME_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if _RUNTIME_SETTINGS_FILE.exists():
            try:
                existing = json.loads(_RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.update(validated)
        _RUNTIME_SETTINGS_FILE.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except PermissionError:
        return {"ok": False, "msg": "写入配置失败: 权限不足，请检查文件权限"}
    except OSError as e:
        return {"ok": False, "msg": f"写入配置失败: 系统错误 - {e}"}
    except Exception as e:
        return {"ok": False, "msg": f"写入配置失败: {type(e).__name__} - {e}"}
    # 触发 bot 热加载
    _SETTINGS_RELOAD_TRIGGER.write_text(f"settings updated at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    changed = ", ".join(validated.keys())
    logger.info(f"Settings updated: {changed}")
    return {"ok": True, "msg": f"已保存 {len(validated)} 项参数，Bot 将在下一条消息时生效"}
