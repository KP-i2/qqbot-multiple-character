"""
QQ 富媒体消息解析（小程序 / 分享卡片 / XML 消息）
"""

import json
import re

from nonebot.adapters.onebot.v11 import Event


def parse_rich_message(event: Event) -> str:
    """从消息中提取文本，包括 QQ 小程序 / 分享卡片 / XML 消息的可读内容。"""
    parts = []
    for seg in event.message:
        if seg.type == "text":
            t = seg.data.get("text", "").strip()
            if t:
                parts.append(t)

        elif seg.type == "json":
            data_str = seg.data.get("data", "")
            try:
                data = json.loads(data_str) if isinstance(data_str, str) else data_str
                prompt = data.get("prompt", "")
                if prompt:
                    parts.append(prompt)
                else:
                    # 尝试提取 meta 里的描述
                    meta = data.get("meta", {})
                    desc = ""
                    if isinstance(meta, dict):
                        for v in meta.values():
                            if isinstance(v, dict):
                                desc = v.get("desc", "") or v.get("title", "") or v.get("tag", "")
                                if desc:
                                    break
                    app_name = data.get("app", "")
                    view = data.get("view", "")
                    if desc or app_name:
                        label = f"[分享卡片"
                        if app_name:
                            label += f" ({app_name})"
                        label += "]"
                        parts.append(f"{label} {desc}" if desc else label)
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

        elif seg.type == "xml":
            data_str = seg.data.get("data", "")
            if data_str:
                # 从 XML 中提取标题和描述
                for tag in ("title", "brief", "source"):
                    m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', data_str, re.DOTALL)
                    if m:
                        val = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                        if val and len(val) > 2:
                            parts.append(val)

        elif seg.type == "share":
            title = seg.data.get("title", "")
            url = seg.data.get("url", "")
            content = seg.data.get("content", "")
            if title:
                share_text = f"[分享链接] {title}"
                if content:
                    share_text += f" - {content}"
                if url:
                    share_text += f" {url}"
                parts.append(share_text)

        elif seg.type not in ("at", "image", "face", "record", "video",
                              "rps", "dice", "shake", "poke", "anonymous",
                              "contact", "location", "music", "reply",
                              "forward", "node"):
            # 未知类型，尝试字符串化
            s = str(seg).strip()
            if s and len(s) > 3 and not s.startswith("[CQ:"):
                parts.append(s)

    return " ".join(parts).strip()
