"""
情绪表情系统：加载、检测、发送
"""

import base64
import logging
import os
import random
import time
from pathlib import Path

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment

from . import config as cfg


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

# 运行时动态数据（由 load_emoji_files 填充）
EMOJI_FILES: dict[str, list[Path]] = {}       # emotion_name -> [image_paths]
EMOJI_EMOTIONS: dict[str, list[str]] = {}     # emotion_name -> [keywords]

_log = logging.getLogger("zyw_chat.events")


def load_emoji_files():
    """扫描表情目录，自动发现所有子文件夹并加载图片和关键词。

    每个子文件夹：
    - 文件夹名 = 情绪类别
    - keywords.txt = 关键词文件（每行一个关键词，# 开头为注释）
    - 图片文件 = jpg/jpeg/png/gif/webp
    """
    global EMOJI_FILES, EMOJI_EMOTIONS
    EMOJI_FILES = {}
    EMOJI_EMOTIONS = {}

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
        EMOJI_FILES[emotion] = imgs

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
                    EMOJI_EMOTIONS[emotion] = keywords
                    logger.info(f"[EMOJI] {emotion}: {len(imgs)} 图片, {len(keywords)} 关键词 (from keywords.txt)")
                else:
                    EMOJI_EMOTIONS[emotion] = _EMOJI_DEFAULT_KEYWORDS.get(emotion, [])
                    logger.info(f"[EMOJI] {emotion}: {len(imgs)} 图片, keywords.txt 为空，使用默认关键词")
            except Exception as e:
                logger.warning(f"[EMOJI] 读取 {emotion}/keywords.txt 失败: {e}")
                EMOJI_EMOTIONS[emotion] = _EMOJI_DEFAULT_KEYWORDS.get(emotion, [])
        else:
            EMOJI_EMOTIONS[emotion] = _EMOJI_DEFAULT_KEYWORDS.get(emotion, [])
            logger.info(f"[EMOJI] {emotion}: {len(imgs)} 图片, 使用默认关键词")

    total_imgs = sum(len(v) for v in EMOJI_FILES.values())
    emotions = list(EMOJI_FILES.keys())
    logger.info(f"[EMOJI] 加载完成: {total_imgs} 个表情, {len(emotions)} 种情绪: {emotions}")


def detect_emotion(user_text: str, bot_reply: str) -> str | None:
    """根据用户消息和 bot 回复检测情绪，多个命中时随机选一个。"""
    combined = (user_text + " " + bot_reply).lower()
    matched = []
    for emotion, keywords in EMOJI_EMOTIONS.items():
        for kw in keywords:
            if kw.lower() in combined:
                matched.append(emotion)
                break  # 同一情绪只计一次
    return random.choice(matched) if matched else None


async def maybe_send_emoji(bot: Bot, event: Event, user_text: str, bot_reply: str, uid: str):
    """根据对话情绪概率发送表情图片。"""
    # 冷却检查（同一用户 60 秒内最多发一次表情）
    now = time.time()
    last_sent = _EMOJI_COOLDOWN.get(uid, 0)
    if now - last_sent < 60:
        return

    # 概率判断
    if random.random() > _EMOJI_PROBABILITY:
        return

    # 情绪检测
    emotion = detect_emotion(user_text, bot_reply)
    if not emotion:
        return

    # 获取对应情绪的表情文件
    files = EMOJI_FILES.get(emotion, [])
    if not files:
        return

    # 随机选一张
    img_path = random.choice(files)
    try:
        # GIF 用 base64 发送以保证动画效果，其他格式用 file URI
        if img_path.suffix.lower() == '.gif':
            img_bytes = img_path.read_bytes()
            b64 = base64.b64encode(img_bytes).decode('ascii')
            await bot.send(event, MessageSegment.image(file=f"base64://{b64}"))
        else:
            file_uri = f"file:///{img_path.as_posix()}"
            await bot.send(event, MessageSegment.image(file=file_uri))
        _EMOJI_COOLDOWN[uid] = now
        _log.info(f"[EMOJI] 发送表情: emotion={emotion}, file={img_path.name}, user={uid}")
    except Exception as e:
        logger.warning(f"[EMOJI] 发送失败: {e}")
