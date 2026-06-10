"""
用户画像（User Profile）：提取、缓存、注入
"""

import asyncio
import json
import re
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

from nonebot import logger

from . import config as cfg


# ── 用户画像存储 ──
_PROFILE_DIR = cfg.QQBOT_DIR / "data" / "user_profiles"
_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
_PROFILE_UPDATE_INTERVAL = 3600   # 至少 1 小时更新一次
_PROFILE_UPDATE_MIN_TURNS = 10    # 累积 10 轮对话才触发
profile_last_update: dict[str, float] = {}
profile_turn_count: dict[str, int] = defaultdict(int)
_profile_cache: dict[str, dict] = {}
_profile_semaphore = asyncio.Semaphore(2)  # 画像提取最多 2 个并发


def load_user_profile(uid: str) -> Optional[dict]:
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


def save_user_profile(uid: str, profile: dict):
    """保存用户画像到磁盘和内存缓存"""
    try:
        profile["updated_at"] = datetime.now().isoformat()
        path = _PROFILE_DIR / f"{uid}.json"
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        _profile_cache[uid] = profile
        cfg.chat_logger.info(f"[PROFILE] 已保存 uid={uid}, summary={profile.get('profile_summary', '')[:60]}")
    except Exception as e:
        logger.warning(f"[PROFILE] 保存失败 uid={uid}: {e}")


async def extract_user_profile(uid: str, history_key: str, skill_name: str):
    """后台任务：从对话历史中提取用户画像（增量更新）"""
    # 延迟导入避免循环依赖
    from . import history as hist
    from . import api_client
    from . import provider as prov

    try:
        await asyncio.wait_for(_profile_semaphore.acquire(), timeout=30)
    except asyncio.TimeoutError:
        cfg.chat_logger.warning(f"[PROFILE] 信号量超时 uid={uid}，跳过")
        return

    try:
        # 取最近 10 轮对话
        history = hist.conversation_histories.get(history_key, [])
        recent = history[-20:]
        if len(recent) < 4:
            return

        # 加载现有画像
        existing = load_user_profile(uid) or {}
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
            "model": cfg.DEEPSEEK_SEARCH_MODEL,
            "temperature": 0.3,
            "max_tokens": 512,
            "messages": [
                {"role": "system", "content": extraction_prompt},
                {"role": "user", "content": user_content},
            ],
        }

        data = await api_client.api_request(payload, prov.get_provider("deepseek"))
        if data is None:
            return

        raw_content = data["choices"][0]["message"]["content"].strip()
        # 清洗可能的 markdown 代码块
        raw_content = re.sub(r'^```(?:json)?\s*', '', raw_content)
        raw_content = re.sub(r'\s*```$', '', raw_content)

        try:
            extracted = json.loads(raw_content)
        except json.JSONDecodeError:
            cfg.chat_logger.warning(f"[PROFILE] JSON 解析失败 uid={uid}: {raw_content[:100]}")
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
        skill_data["turns"] = skill_data.get("turns", 0) + profile_turn_count.get(uid, 0)
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
            "total_turns": existing.get("total_turns", 0) + profile_turn_count.get(uid, 0),
            "per_skill": all_per_skill,
        }

        save_user_profile(uid, profile)

    except Exception as e:
        logger.warning(f"[PROFILE] 提取异常 uid={uid}: {e}")
    finally:
        _profile_semaphore.release()


def get_profile_summary(uid: str, skill_name: str) -> str:
    """获取用户画像摘要文本，用于注入 system prompt"""
    profile = load_user_profile(uid)
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
