"""
消息工具：分段、QQ 表情规范化与解析
"""

import re

from nonebot.adapters.onebot.v11 import Message, MessageSegment


# ============================================================
# 消息分段
# ============================================================

_SPLIT_THRESHOLD = 120   # 超过此字符数才分段
_SPLIT_MAX_SEGMENTS = 3   # 最多分几段
_SPLIT_MIN_SEGMENT = 40   # 每段最少字符数


def split_message(text: str) -> list[str]:
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
QQ_FACE_ALIASES: dict[str, str] = {
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


def clean_chat_output(text: str) -> str:
    """清理聊天输出：压缩多余空行、去除每行首尾空白。
    用于将 LLM 的 Markdown 风格输出转换为适合 QQ 聊天的紧凑格式。
    """
    # 压缩连续换行（\n\n → \n）
    text = re.sub(r'\n{2,}', '\n', text)
    # 去除每行首尾空白
    lines = [line.strip() for line in text.split('\n')]
    # 去除空行
    lines = [line for line in lines if line]
    return '\n'.join(lines)


def normalize_qq_faces(text: str) -> str:
    """将 AI 回复中的表情文本规范化为 NTQQ 可识别的格式。
    处理 [微笑]、【微笑】、（微笑）等变体，统一为 [标准名]。
    仅做精确别名匹配，避免误改正常文本。
    """
    def _replace_face(m):
        raw = m.group(1).strip()
        if raw in QQ_FACE_ALIASES:
            return f"[{QQ_FACE_ALIASES[raw]}]"
        return m.group(0)  # 不认识就原样保留

    # 匹配 【】、（） 包裹的疑似表情 → 转为 [] 格式
    text = re.sub(r'[【（]\s*([^】）]{1,6}?)\s*[】）]', _replace_face, text)
    # 处理 [] 包裹但别名不同的情况（如 [笑脸] → [微笑]）
    text = re.sub(r'\[\s*([^[\]]{1,6}?)\s*\]', _replace_face, text)
    return text


# QQ 表情名 → face ID 映射（NapCat QSid 标准名）
QQ_FACE_ID_MAP: dict[str, int] = {
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
    # NapCat 标准名与 QQ_FACE_ALIASES 不匹配的补充：
    "强": 76, "弱": 77, "亲亲": 109, "嘿哈": 264, "思考": 269,
    "捂嘴笑": 183, "饭": 61, "菜刀": 112, "西瓜": 89, "啤酒": 113,
    "咖啡": 60, "猪头": 46, "玫瑰": 63, "凋谢": 64, "示爱": 116,
    "爱心": 66, "拥抱": 49, "胜利": 79, "勾引": 119, "差劲": 121,
}


def parse_qq_faces(text: str) -> Message:
    """将文本中的 [表情名] 解析为 QQ 原生表情段 + 文本段的混合 Message。
    先调用 normalize_qq_faces 规范化别名，再查找 QQ_FACE_ID_MAP 获取 face ID。
    未识别的 [表情名] 保留为文本（由 QQ 客户端尝试本地渲染）。
    """
    text = normalize_qq_faces(text)
    msg = Message()

    # 先提取 URL，避免 URL 中的方括号被误解析为表情
    url_pattern = re.compile(r'(https?://[^\s<>]+)')
    parts = url_pattern.split(text)

    for part in parts:
        if url_pattern.match(part):
            # URL 部分直接作为文本发送，确保可点击
            msg += MessageSegment.text(part)
        else:
            # 非 URL 部分按原逻辑解析表情
            pattern = re.compile(r'\[([^\[\]]{1,8})\]')
            last_end = 0
            for m in pattern.finditer(part):
                before = part[last_end:m.start()]
                if before:
                    msg += MessageSegment.text(before)
                face_name = m.group(1)
                face_id = QQ_FACE_ID_MAP.get(face_name)
                if face_id is not None:
                    msg += MessageSegment.face(face_id)
                else:
                    msg += MessageSegment.text(m.group(0))
                last_end = m.end()
            remaining = part[last_end:]
            if remaining:
                msg += MessageSegment.text(remaining)

    return msg if msg else Message(text)
