"""
DeepSeek DSML / 原生 XML 工具调用标记清洗
"""

import logging
import re


# ── DeepSeek DSML 标记清洗 ──
# DeepSeek 有时会在 content 中嵌入工具调用的 XML 标记（｜｜DSML｜｜tool_calls...），
# 需要在使用 tools 时清洗掉这些原始标记，避免泄露到最终回复。
_DSML_PATTERN = re.compile(
    r'[\uff5c|]*\s*DSML\s*[\uff5c|]*\s*tool_calls\s*>.*?'
    r'[\uff5c|]*\s*/?\s*DSML\s*[\uff5c|]*\s*/?\s*tool_calls\s*>',
    re.DOTALL
)
_DSML_INVOKE_PATTERN = re.compile(
    r'[\uff5c|]*\s*DSML\s*[\uff5c|]*\s*invoke[^>]*>.*?'
    r'[\uff5c|]*\s*/?\s*DSML\s*[\uff5c|]*\s*/?\s*invoke\s*>',
    re.DOTALL
)
_DSML_PARAM_PATTERN = re.compile(
    r'[\uff5c|]*\s*DSML\s*[\uff5c|]*\s*parameter[^>]*>.*?'
    r'[\uff5c|]*\s*/?\s*DSML\s*[\uff5c|]*\s*/?\s*parameter\s*>',
    re.DOTALL
)
# 通用兜底：匹配所有包含 DSML 的尖括号标签
_DSML_ANY_TAG = re.compile(r'<[^>]*DSML[^>]*/?>',  re.IGNORECASE)
# 截断兜底：匹配从 DSML 开标签到字符串末尾（处理模型输出被截断的情况）
_DSML_TRUNCATED = re.compile(
    r'<[\uff5c|]*\s*DSML\s*[\uff5c|].*',
    re.DOTALL
)
# ── 原生 tool_calls XML 清洗 ──
_TOOL_CALLS_FULL = re.compile(
    r'<tool_calls>.*?</tool_calls>',
    re.DOTALL
)
_TOOL_CALL_TRUNCATED = re.compile(
    r'<tool_call[^>]*>.*',
    re.DOTALL
)
_TOOL_CALLS_OPEN_TRUNC = re.compile(
    r'<tool_calls>.*',
    re.DOTALL
)
# ── 通用工具调用标签清洗 ──
_TOOL_LIKE_TAG = re.compile(
    r'</?\s*(?:web_search|search_corpus|search|tool[a-z_]*|parameters?|query)\b[^>]*/?>',
    re.IGNORECASE
)
# ── 模型把工具调用写成正文的 XML 变体 ──
# 实测 DeepSeek 会在 content 里输出：
#   <system>web_search</system>
#   <parameter name="query">...</parameter>
# 也见过：
#   <tool_uses><tool_use><tool_name>web_search</tool_name><parameters><query>...</query>
# 这不是正常回复，必须整块剥掉；否则会原样发给用户。
_TOOL_USES_BLOCK = re.compile(
    r'<\s*tool_uses?\b[^>]*>.*?<\s*/\s*tool_uses?\s*>',
    re.DOTALL | re.IGNORECASE
)
_TOOL_SYSTEM_BLOCK = re.compile(
    r'<\s*system\b[^>]*>.*?<\s*/\s*system\s*>',
    re.DOTALL | re.IGNORECASE
)
_TOOL_PARAMETER_BLOCK = re.compile(
    r'<\s*(?:parameter|parameters)\b[^>]*>.*?<\s*/\s*(?:parameter|parameters)\s*>',
    re.DOTALL | re.IGNORECASE
)
_TOOL_NAME_BLOCK = re.compile(
    r'<\s*tool_name\b[^>]*>.*?<\s*/\s*tool_name\s*>',
    re.DOTALL | re.IGNORECASE
)
_TOOL_QUERY_BLOCK = re.compile(
    r'<\s*query\b[^>]*>.*?<\s*/\s*query\s*>',
    re.DOTALL | re.IGNORECASE
)
_TOOL_FUNCTION_BLOCK = re.compile(
    r'<\s*function\b[^>]*>.*?<\s*/\s*function\s*>',
    re.DOTALL | re.IGNORECASE
)
# 兜底：这些开标签一旦出现，基本可判定为工具调用泄露；删到末尾
_TOOL_MARKUP_TRUNC = re.compile(
    r'<\s*(?:system|parameter|parameters|function|tool[a-z_]*|query)\b.*',
    re.DOTALL | re.IGNORECASE
)
_TOOL_MARKUP_DETECT = re.compile(
    r'<\s*(?:system|parameter|parameters|function|tool[a-z_]*|query)\b|DSML|<\s*\|[^>]*\|>',
    re.IGNORECASE
)


def contains_tool_markup(text: str) -> bool:
    """正文里是否混入了工具调用/系统标记（用于流式路径提前丢弃）。"""
    return bool(_TOOL_MARKUP_DETECT.search(text or ""))
# ── 思考块清洗 ──
# 部分 LLM (minimax M3 等) 把 reasoning 字段塞到 content 前面,以 <think>...</think> 块呈现,
# 若不剥掉会原样发给用户。兼容 Anthropic / DeepSeek / OpenAI o1 三家风格。
_THINK_BLOCK_FULL = re.compile(r'<think>.*?</think>', re.DOTALL)
# 截断兜底: <think> 开标签但没有 </think> 闭合(模型输出被 max_tokens 切断)
_THINK_BLOCK_TRUNC = re.compile(r'<think>.*', re.DOTALL)
# 思考结果前缀(如 Claude 的 <thinking>...</thinking>)
_THINK_BLOCK_ALT = re.compile(r'<thinking>.*?</thinking>', re.DOTALL)
_THINK_BLOCK_ALT_TRUNC = re.compile(r'<thinking>.*', re.DOTALL)


def strip_dsml_markup(text: str) -> str:
    """清洗 DeepSeek 可能嵌入 content 的工具调用标记（DSML 和原生 XML）"""
    # DSML 格式
    text = _DSML_PATTERN.sub('', text)
    text = _DSML_INVOKE_PATTERN.sub('', text)
    text = _DSML_PARAM_PATTERN.sub('', text)
    text = _DSML_ANY_TAG.sub('', text)
    # 原生 <tool_calls> XML 格式
    text = _TOOL_CALLS_FULL.sub('', text)
    text = _TOOL_CALL_TRUNCATED.sub('', text)
    # <system>web_search</system><parameter ...> 这类正文工具调用
    # 必须先整块删除，再删零散标签，否则外层标签先被删掉、内容会漏出来
    text = _TOOL_USES_BLOCK.sub('', text)
    text = _TOOL_SYSTEM_BLOCK.sub('', text)
    text = _TOOL_PARAMETER_BLOCK.sub('', text)
    text = _TOOL_NAME_BLOCK.sub('', text)
    text = _TOOL_QUERY_BLOCK.sub('', text)
    text = _TOOL_FUNCTION_BLOCK.sub('', text)
    # 通用工具调用标签（GPT-5.5 等模型模仿 function calling）
    text = _TOOL_LIKE_TAG.sub('', text)
    # 思考块(多模型通用: minimax M3 / Claude / DeepSeek 风格),防止模型内心独白泄露给用户
    text = _THINK_BLOCK_FULL.sub('', text)
    text = _THINK_BLOCK_TRUNC.sub('', text)
    text = _THINK_BLOCK_ALT.sub('', text)
    text = _THINK_BLOCK_ALT_TRUNC.sub('', text)
    # 截断兜底：如果还有残留的 DSML / tool_calls / system 开标签，删除从那里到末尾
    text = _DSML_TRUNCATED.sub('', text)
    text = _TOOL_CALLS_OPEN_TRUNC.sub('', text)
    text = _TOOL_MARKUP_TRUNC.sub('', text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def clean_llm_reply(raw_content: str) -> str:
    """清洗模型回复，处理截断的 DSML 标记等异常情况。

    模型在工具调用轮次后可能仍尝试生成 DSML 标记，但因 max_tokens
    截断而只残留不完整的尖括号内容（如 '<'）。此函数负责清理这些
    残留并保证返回有意义的文本。
    """
    cleaned = strip_dsml_markup(raw_content)
    # 去除尾部残留的不完整 XML-like 标签（如 '<'、'</'、'<tag' 无闭合 '>'）
    cleaned = re.sub(r'<[^>]*$', '', cleaned).strip()
    # 如果清洗后内容过短或无实质内容，返回兜底消息
    if len(cleaned) <= 1 or not re.search(r'[\w\u4e00-\u9fff]', cleaned):
        logging.getLogger("char_chat.events").warning(
            f"[LLM] 模型回复清洗后为空或无意义 (原始: {repr(raw_content[:200])})"
        )
        return "⚠️ 响应解析失败，请重发或换个问法。"
    return cleaned
