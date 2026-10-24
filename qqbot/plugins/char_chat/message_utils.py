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


# ── 2026-07-03: 颜文字风格语义断点识别 ──
# 传统 _sentence_end 只认"。"等 6 个字符,遇到颜文字/emoji/省略号就"卡"住,导致
# 一段堆到 200+ 字才切,下一段只剩尾巴。这里用更宽的断点正则,再贪心切分。

# 在字符层识别的"自然句末"标点(单字符)
# 2026-07-03 修正: 移除 '\n' — LLM 单 \n 输出很常见, 把它当句末会把句子切碎
_NATURAL_TAIL_CHARS = set("。！？!?…") | {
    "♡", "♥", "❤", "❣", "❥",  # 心形
    "💙", "💕", "💖", "💗", "💘", "💝", "💞", "💟", "💓", "💔", "💛", "💚", "💜", "🧡", "🤍", "🖤", "🤎",  # 彩色心
    "🌙", "⭐", "🌟", "✨", "💫", "🌸", "🌺", "🌷", "🌹",  # 装饰
    "🌈", "🎀", "🎉", "🎊", "🎈", "🎂", "🎁",  # 庆祝
    "🐾", "🐱", "🐰", "🐶", "🦊", "🐻", "🐼", "🐨", "🐯", "🦁", "🐮", "🐷",  # 动物
    "❄", "☀", "☁", "☂", "☔", "⚡", "🔥", "💧", "🌊",  # 自然
}

# 在 token 层识别的"自然句末"模式(可多字符)
_NATURAL_TAIL_PATTERNS = [
    re.compile(r"[‼⁉]+"),                        # ‼️‼️ 连续感叹
    re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹̴̷͈]+"),           # 上标 + 组合用装饰(如 ど⁰̷̴͈꒨⁰̷̴͈う)
    re.compile(r"[\(（][^()（）]{1,8}[\)）]"),  # 颜文字 (...)（...）
    re.compile(r"[!?！？]{2,}"),                  # !!?? 连续
    re.compile(r"\.{2,}"),                        # ...
    re.compile(r"…{1,}"),                         # …… 省略
    re.compile(r"う[♡♥❤]"),                       # 日式"う♡"
    re.compile(r"[のねよだよ]♡"),                 # 日文假名 + ♡
    re.compile(r"\(๑[^()（）]{0,8}๑\)"),          # (๑• . •๑)
    re.compile(r"\(´[^()（）]{0,8}`\)"),          # (´･ω･`)
    re.compile(r"\(\*[^()（）]{0,8}\*\)"),        # (*≧▽≦)
    re.compile(r"\(⁰[^()（）]{0,12}\)"),          # (⁰̷̴͈꒨⁰̷̴͈う) 类装饰闭合
]


def _is_natural_tail(s: str, pos: int) -> bool:
    """判断 s[pos:] 是否以"自然句末"结尾(单字符或 token)。
    pos 是 s 中某个 token 的结束位置(即 s[pos-1] 是末字符)。
    """
    if pos <= 0 or pos > len(s):
        return False
    # 单字符检查
    if s[pos - 1] in _NATURAL_TAIL_CHARS:
        return True
    # 多字符模式检查(从 pos 往前倒推 1-12 个字符)
    tail_window = s[max(0, pos - 12):pos]
    for pat in _NATURAL_TAIL_PATTERNS:
        if pat.search(tail_window):
            return True
    return False


# 2026-07-06: 阈值降到 12 — 之前 30 会漏掉"先别急着盲目下单。先别急着盲目下单。"
# 这种 8 字 × 2 = 16 字的短复读 (chat.log 7/6 16:43 案例)
_DEDUP_MIN_CHUNK = 12

# 2026-07-22: Pass 3 短词阈值 (独立于 _DEDUP_MIN_CHUNK, 抓"我搜一下等下我搜一下等下"这种
# 6 字符 × 2 但无句末标点的复读 — Pass 1/Pass 2 都漏抓的场景)
_DEDUP_MIN_CHUNK_PASS3 = 4


def _is_natural_repeat(chunk: str) -> bool:
    """2026-07-22: 判断 chunk 是不是"自然重复" (笑声/单字符重复), 不是 LLM 复读.

    防御: 不要把"哈哈哈"+"哈哈哈" 这种自然重复误删.
    判定规则:
    - chunk 长度 <= 2 字符 → 算自然重复 (笑声/单字)
    - chunk 里所有字符都是同一类且种类数 <= 1 → 算自然重复
      例: "哈哈哈" (全'哈'), "lol" (全'l'), "111" (全'1'), "abab" 的种类是 {a,b}=2 不算
    - 长度 3+ 且有 >= 2 种字符 → 视为 LLM 复读 (要删)
      例: "我爱你" (种类 {我,爱,你}=3), "我搜一下等下" (种类很多)
    """
    if len(chunk) <= 2:
        return True
    unique = set(chunk)
    if len(unique) <= 1:
        return True
    return False


def _dedup_short_chunks(text: str, min_chunk: int = _DEDUP_MIN_CHUNK_PASS3, max_iterations: int = 5) -> str:
    r"""2026-07-22: Pass 3 — 短词无标点复读检测.

    抓的场景: 流式累积中"我搜一下等下我搜一下等下" (6 字符 × 2, 无标点)
    Pass 1 漏抓: "我搜一下等下" 6 字符 < _DEDUP_MIN_CHUNK 12
    Pass 2 漏抓: 依赖句末标点, 无标点时直接 break

    算法: 用 regex `(.+?)\s*\1+` 短阈值 (默认 4) 抓复读段, Python regex greedy 会找最长
    可复读段. 防御"自然重复" (笑声/单字符) 走 _is_natural_repeat 子函数.
    """
    if not text or len(text) < min_chunk * 2:
        return text

    # 短词复读一般就 2 次连续重复, max_iterations 不需要太多
    # 用贪婪 pattern 找最长复读段, 防御自然重复 (笑声/单字符)
    pattern = re.compile(rf"(.{{{min_chunk},}})\s*\1+", re.DOTALL)
    for _ in range(max_iterations):
        m = pattern.search(text)
        if not m:
            break
        chunk = m.group(1)
        if _is_natural_repeat(chunk):
            # 自然重复 (笑声/单字符), 不删. 这种情况一般是用户希望保留的.
            break  # 整个 text 都被这个复读段占满, 没有更长的复读可删
        # 复读! 删掉重复部分, 保留第一个 chunk
        text = text[:m.start()] + chunk + text[m.end():]
    return text


def dedup_consecutive_chunks(
    text: str,
    min_chunk: int = _DEDUP_MIN_CHUNK,
    max_iterations: int = 5,
) -> str:
    """删除 LLM 流式输出中的复读(连续重复整段)。

    2026-07-03: 初版, min_chunk=30, 只抓大段复读(>= 30 字 × 2)
        例: "ど⁰̷̴͈꒨⁰̷̴͈う……那你有对别人也这样说话吗 ¿" 复读两遍 → 删后半段

    2026-07-06 修正 (A 方案):
        阈值从 30 降到 12, 覆盖中短复读(8-12 字 × 2 的尾句复读).
        之前漏掉 7/6 16:43 "先别急着盲目下单。先别急着盲目下单。"(8 字 × 2 = 16 字).

    2026-07-06 修正 (C 方案):
        末尾复读 (trailing repetition) 检测 — 在主循环之外多一个 pass,
        抓"text 末尾 = 前面出现过的片段"这种模式. 这是 LLM 复读最常见形态:
        生成完整段落后, 末尾无意识地复读最后一句.
        算法: 从末尾往前找最近的句末标点, 取最后 1-2 句, 在前文中找是否出现过,
        如果出现过且重复 >= 2 次, 删掉重复部分.

    阈值说明:
    - 阈值 12 字会拦下"哈哈哈"+"哈哈哈"这种短复读 — 但实际上"哈哈哈"重复
      在中文对话里很自然, 我们的目的就是删这种"感知重复"的内容.
      如果误删敏感, 可传 min_chunk=20 兜底.

    注意:
    - 只删相邻重复 (LLM 复读); 中间夹了别的内容不算
    - max_iterations 防止死循环
    - re.DOTALL 让 . 匹配换行 — 复读可能跨行
    """
    if not text:
        return text

    # Pass 1: 主算法 — 连续相邻大段复读
    # 2026-07-06: 在 capture 末尾允许 \s* (允许复读段之间空白不一致,
    #   例: "段A\n段B\n" 复读时差一个换行符也能匹配)
    # 2026-07-06: 在 capture 末尾允许 \s* (允许复读段之间空白不一致,
    #   例: "段A\n段B\n" 复读时差一个换行符也能匹配)
    pattern = re.compile(rf"(.{{{min_chunk},}})\s*\1+", re.DOTALL)
    for _ in range(max_iterations):
        new_text, n = pattern.subn(r"\1", text)
        if n == 0:
            break
        text = new_text

    # Pass 2: 末尾复读检测 (2026-07-06 新增, C 方案)
    text = _dedup_trailing_repetition(text, min_chunk=min_chunk)

    # Pass 3: 短词无标点复读检测 (2026-07-22 新增)
    # 抓"我搜一下等下我搜一下等下" (6 字符 × 2, 无句末标点) — Pass 1/Pass 2 都漏抓
    # 防御: _is_natural_repeat 过滤笑声/单字符自然重复
    text = _dedup_short_chunks(text)

    return text


def _dedup_trailing_repetition(text: str, min_chunk: int = _DEDUP_MIN_CHUNK) -> str:
    """2026-07-06 新增: 检测并删除"末尾复读"。

    形态: LLM 在生成完整段落后, 末尾无意识地把最后一句复读一次.
    例: "...先帮你推配置单。先别急着盲目下单。先别急着盲目下单。"
    这里末尾的"先别急着盲目下单。"是倒数第二句的复读.

    算法:
    1. 从 text 末尾往前找最近的句末标点 (。！？!?…), 切出"末尾子句"
    2. 末尾子句至少 max(4, min_chunk//2) 字
    3. 在 body 中查找 tail:
       - tail 必须出现在 body **末尾 50% 范围内** (避免把"开头"误认为"复读")
       - tail 在 body 中出现的位置必须**紧邻 body 末尾** (中间没有其他内容)
    4. 命中后删掉末尾子句 (一次复读); 重复直到无更多复读

    关键防御: "tail 是 body 开头" 不算复读 — LLM 正常行文里首尾呼应不算复读.
    例: "今天讲个故事。故事是...今天讲个故事。" 这种"开头=结尾"是正常写作,
    不是复读, 不能删.
    """
    if not text or len(text) < min_chunk:
        return text

    # 句末标点: 中英日 + emoji 装饰
    sentence_end = re.compile(r"[。！？!?…♡♥❤💙💕💖💗❣✨🌙⭐]+")

    for _ in range(3):
        if not text or len(text) < min_chunk:
            break

        # 从末尾找最后一个句末标点位置
        m = None
        for match in sentence_end.finditer(text):
            m = match  # 迭代到最后一个

        if not m:
            break

        end_pos = m.end()
        # 末尾子句 = text[某个起点:end_pos]
        # 起点: 找前一个句末标点之后
        prev_starts = [mm.end() for mm in sentence_end.finditer(text, 0, end_pos - 1)]
        if prev_starts:
            tail_start = prev_starts[-1]
        else:
            # 整段就是一个句子
            tail_start = 0

        tail = text[tail_start:end_pos]

        # 末尾子句至少要有 max(4, min_chunk//2) 字
        if len(tail) < max(4, min_chunk // 2):
            break

        body = text[:tail_start]
        if not body:
            break

        # ── 关键防御 1: tail 必须紧邻 body 末尾 (中间无其他内容) ──
        if not body.endswith(tail):
            break  # tail 在 body 中间 (不是末尾), 不算复读

        # ── 关键防御 2: 防止"首尾呼应"误判 ──
        # tail 在 body 中出现的位置不能是 body 的开头.
        # 如果 body 去掉末尾 tail 后, 剩下的部分**完全没有句末标点**,
        # 说明 body == tail (单句复读), 这种情况也算了, 继续.
        # 但如果 body 里 tail 之前还有实质内容 (有句末标点), 那 tail 是复读,
        # OK, 继续.
        #
        # 关键: 防止的是"body == tail" 的退化场景 — 即"先别急着盲目下单。先别急着盲目下单。"
        # 这种情况 body 就是一个 tail, 去掉就是空 — 也算复读, 继续.
        #
        # 防止的是 Case 9 那种 "开头 = 结尾" 的"首尾呼应":
        # body = "完整长段。\n今天给大家讲一个故事。", tail = "今天给大家讲一个故事。"
        # 这里 body 有实质内容, tail 是 body 末尾, **不是** 复读.
        # 区分: tail 在 body 中的"前一个出现"必须是紧邻的 (中间没其他内容)
        # 也就是说, body 去掉末尾 tail 之后, 不能再以 tail 结尾 (否则是连续复读).

        # 计算 body 末尾有多少个连续 tail 复读
        n_repeats = 0
        probe = body
        while probe.endswith(tail):
            n_repeats += 1
            probe = probe[: -len(tail)]

        # n_repeats == 0: body 末尾不以 tail 结尾 — 这种情况应该被"防御 1"拦下,
        #   但保险起见再判断一次
        if n_repeats == 0:
            break

        # n_repeats == 1: body 末尾 1 个 tail + text 末尾 1 个 tail = 共 2 个 (1 次复读)
        # n_repeats >= 2: 多次复读
        # 删掉末尾所有 tail, 保留 1 个
        text = probe + tail

    return text


def split_at_breath_pauses(
    text: str,
    min_chars: int = 80,
    max_chars: int = 200,
    hard_max_chars: int = 300,
) -> list[str]:
    """按"语义呼吸点"切分长文本为多段。

    与 split_message 不同: 这里用更宽的断点识别(颜文字/emoji/日式句末),
    且每段长度自适应: 遇到断点就切(若已达 min_chars),但单段不超过 hard_max_chars。

    Args:
        text: 完整回复文本
        min_chars: 达到此长度且遇到断点才切(避免一句话被切两半)
        max_chars: 理想段长上限(达到此长度即使无断点也强制切)
        hard_max_chars: 单段绝对上限(避免极端情况挤在一段)
    """
    text = text.strip()
    if not text:
        return []

    # 2026-07-03: 把 LLM 输出里的单 \n 升级为 \n\n(只要它前后不是空白行),
    # 这样后续按 \n\n 拆段就能正确识别 LLM 的段落分隔意图。
    # 允许 \n 后有空格: "A\n B" → "A\n\nB" (LLM 常加缩进空格)
    text = re.sub(r"([^\n])\n\s*([^\n])", r"\1\n\n\2", text)

    # 先按双换行拆大块(这些是 LLM 自己留的段落分隔)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not blocks:
        return []

    segments: list[str] = []

    def _split_block(block: str) -> list[str]:
        """在单段落内贪心切分 — 只在段落本身过长(> max_chars)时才细分。
        2026-07-03: LLM 用 \n 已经表达了"我想分段"的意图, 我们尊重它, 不主动细分短段落。
        """
        if len(block) <= max_chars:
            return [block]
        result: list[str] = []
        buf = ""
        i = 0
        # 用 char 级扫描,每字符检查"是否是自然断点"
        n = len(block)
        while i < n:
            buf += block[i]
            i += 1
            # 达到硬上限: 立刻切
            if len(buf) >= hard_max_chars:
                result.append(buf.strip())
                buf = ""
                continue
            # 在 min_chars ~ max_chars 之间: 遇到断点才切
            if len(buf) >= min_chars and _is_natural_tail(buf, len(buf)):
                result.append(buf.strip())
                buf = ""
                continue
            # 超过 max_chars 但没遇到断点: 强制切
            if len(buf) >= max_chars:
                result.append(buf.strip())
                buf = ""
        if buf.strip():
            result.append(buf.strip())
        return result

    # 2026-07-03: 尊重 LLM 用 \n 划分的段落 — 只把"过短"(< min_chars)的短段落合并到上一段,
    # 不再把超长段细分太多。整体限制: 最多 5 段(避免 LLM 偶尔输出 8+ 段)
    _MAX_TOTAL_SEGMENTS = 5
    for blk in blocks:
        if not blk:
            continue
        # 段落本身很短(< min_chars),直接合并到上一段或新建
        if len(blk) < min_chars and segments:
            segments[-1] = segments[-1] + "\n\n" + blk
        else:
            segments.extend(_split_block(blk))

    # 段数过多时,把多出来的尾段合并到倒数第二段(避免 LLM 偶尔生成 8+ 段刷屏)
    while len(segments) > _MAX_TOTAL_SEGMENTS:
        segments[-2] = segments[-2] + "\n\n" + segments[-1]
        segments.pop()

    return [s for s in segments if s]


def split_message(text: str) -> list[str]:
    """将长回复拆分为最多 _SPLIT_MAX_SEGMENTS 段。
    优先按空行分段，其次按句号/换行分段。

    2026-07-03: 此函数被 split_at_breath_pauses 替代用于流式输出场景,
    保留作为旧路径(非流式 / web 搜索回复)的兜底。
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
