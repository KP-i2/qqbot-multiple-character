"""
Web Search 模块 — 基于 DuckDuckGo 的免费网络搜索
无需 API Key，直接可用。
"""

from ddgs import DDGS
from typing import Optional


def web_search(query: str, max_results: int = 5, region: str = "cn-zh") -> list[dict]:
    """
    执行网络搜索，返回结果列表。

    Args:
        query: 搜索关键词
        max_results: 返回结果数量（默认 5）
        region: 搜索区域（默认 cn-zh 中文）

    Returns:
        [{"title": ..., "url": ..., "body": ...}, ...]
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(
                query,
                region=region,
                max_results=max_results,
            ))
        return results
    except Exception as e:
        print(f"[web_search] 搜索失败: {e}")
        return []


def web_news(query: str, max_results: int = 5) -> list[dict]:
    """搜索新闻"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.news(
                query,
                max_results=max_results,
            ))
        return results
    except Exception as e:
        print(f"[web_news] 新闻搜索失败: {e}")
        return []


def format_results(results: list[dict]) -> str:
    """将搜索结果格式化为文本，方便塞进 LLM prompt"""
    if not results:
        return "未找到相关结果。"

    lines = ["## 网络搜索结果\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "无标题")
        url = r.get("href") or r.get("url") or r.get("link", "")
        body = r.get("body") or r.get("snippet", "")
        lines.append(f"[{i}] {title}")
        lines.append(f"    链接: {url}")
        lines.append(f"    摘要: {body}")
        lines.append("")
    return "\n".join(lines)


# 可以直接测试
if __name__ == "__main__":
    q = input("搜索: ")
    results = web_search(q)
    print(format_results(results))
