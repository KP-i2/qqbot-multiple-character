# -*- coding: utf-8 -*-
"""回复输出校验：拦截工具调用泄露、乱码和明显的解析失败文本。"""

import re

from .dsml_cleaner import contains_tool_markup

_BAD_PREFIXES = (
    "⚠️ 响应解析失败",
    "⚠️ LLM 调用失败",
    "响应解析失败",
    "LLM 调用失败",
)
_GARBLED_RE = re.compile(r"\?{4,}|\ufffd{2,}")
_HAS_CONTENT_RE = re.compile(r"[\w\u4e00-\u9fff]")


def validate_reply(text: str) -> tuple[bool, str]:
    """返回 (是否可发送, 失败原因)。只做确定性检查，不调用模型。"""
    t = (text or "").strip()
    if not t:
        return False, "empty"
    if contains_tool_markup(t):
        return False, "tool_markup"
    if any(t.startswith(p) for p in _BAD_PREFIXES):
        return False, "error_text"
    if _GARBLED_RE.search(t):
        return False, "garbled"
    if not _HAS_CONTENT_RE.search(t):
        return False, "no_content"
    return True, ""
