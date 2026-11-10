"""
偶像活动检索（P2 搜索增强）—— 2026-11-10

数据源：
- 花音街（huayinjie.com）：团体页“即将出演” + 活动页详情
- 秀动（showstart.com）：event 页/摘要
- DeepSeek Search 官方/微博/B站补搜

给模型的工具：search_events(entity, city, days)
只查“未来 N 天”的明确活动；没写团体名的日期不推断，交给模型说“未查到”。
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from datetime import datetime, timedelta

import httpx
from nonebot import logger

from . import search

EVENT_TOOL = {
    "type": "function",
    "function": {
        "name": "search_events",
        "description": (
            "【偶像活动检索】查角色/团体未来几天的演出、Live、公演行程。"
            "数据源：花音街（huayinjie.com）、秀动（showstart.com）、官方微博/B站补搜。"
            "用户问“这周/最近/几号有什么活动/演出/行程”时优先用本工具，"
            "不要只拿一条活动汇总微博就下结论。"
            "返回日期、时间、活动名、场馆、状态和来源。"
            "只使用明确写出的日期/团体；某天没写团体名就回答“未查到”，不要推断成别的团。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "团体/角色名，如“午前4時”“StarWinK”；有别名可一起写。",
                },
                "city": {
                    "type": "string",
                    "description": "城市，可选；如“广州”。",
                    "default": "",
                },
                "days": {
                    "type": "integer",
                    "description": "查未来几天，默认 7，最多 30。问“本周”用 7 即可。",
                    "default": 7,
                },
            },
            "required": ["entity"],
        },
    },
}

_CACHE: dict[tuple[str, int, str], tuple[float, str]] = {}
_CACHE_TTL = 300
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def enabled_for_skill(skill_name: str) -> bool:
    """角色 SKILL 里 event_search: true 才挂这个工具。"""
    try:
        from . import skill_manager
        skill = skill_manager.ALL_SKILLS.get(skill_name)
        return bool(skill and getattr(skill, "event_search", False))
    except Exception:
        return False


def _plain(html_text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html_text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_dt(text: str, default_year: int | None = None) -> datetime | None:
    m = re.search(r"(20\d{2})[/.\-](\d{1,2})[/.\-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0); mm = int(m.group(5) or 0)
    else:
        m = re.search(r"(?:(\d{1,2})月)?(\d{1,2})日(?:\s*(\d{1,2}):(\d{2}))?", text)
        if not m:
            return None
        y = default_year or datetime.now().year
        mo = int(m.group(1) or datetime.now().month)
        d = int(m.group(2))
        hh = int(m.group(3) or 0); mm = int(m.group(4) or 0)
    try:
        return datetime(y, mo, d, hh, mm)
    except ValueError:
        return None


def _in_range(dt: datetime | None, start: datetime, end: datetime) -> bool:
    return bool(dt and start.date() <= dt.date() <= end.date())


def _clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", str(title or "")).strip(" ·-—")
    t = re.sub(r"^(?:即将出演|Live|出演安排)\s*", "", t)
    t = re.sub(r"^.*?出演安排\s*", "", t)
    t = re.sub(r"^(?:搜索 投稿 帮助.*?Live|正在加载页面.*?Live)\s*", "", t)
    return t.strip(" ·-—")


def _extract_group_events(url: str, page: str) -> list[dict]:
    """从花音街团体页的“即将出演与近期演出记录”抽活动。

    优先解析 <a class="detail-field-link" href="/lives/..."> 结构，
    这样能拿到活动链接和 venue（团体页 meta 里没有 venue 的再补抓活动页）。
    """
    events: list[dict] = []
    for m in re.finditer(
        r'<a[^>]+class="[^"]*detail-field-link[^"]*"[^>]+href="(/lives/[^"]+)"[^>]*>([\s\S]*?)</a>',
        page,
    ):
        href, inner = m.group(1), m.group(2)
        title_m = re.search(r"<strong>(.*?)</strong>", inner, re.S)
        meta_m = re.search(r"<small>(.*?)</small>", inner, re.S)
        if not title_m or not meta_m:
            continue
        title = _clean_title(_plain(title_m.group(1)))
        meta = _plain(meta_m.group(1))
        dt = _parse_dt(meta)
        if not dt or not title:
            continue
        fields = [x.strip() for x in meta.split(" · ") if x.strip()]
        status = next((x for x in fields if x in ("已发布", "待确认", "已取消", "已结束")), "")
        venue = " · ".join(x for x in fields if x != status and not re.match(r"^20\d{2}/", x))
        events.append({
            "date": dt, "date_text": dt.strftime("%Y/%m/%d %H:%M"), "title": title,
            "venue": venue, "status": status, "source": "花音街",
            "url": "https://www.huayinjie.com" + href.split("?")[0],
        })

    if events:
        return events

    # 兜底：文本切分（页面结构变化时仍能拿到日期/标题）
    plain = _plain(page)
    marker = "即将出演与近期演出记录"
    pos = plain.find(marker)
    if pos < 0:
        pos = plain.find("即将出演")
    if pos < 0:
        return []
    seg = plain[pos + len(marker):] if marker in plain[pos:pos + len(marker) + 2] else plain[pos + len("即将出演"):]
    end = seg.find("近期演出")
    if end > 0:
        seg = seg[:end]
    seg = seg[:4000]

    events = []
    for raw in re.split(r"\s*→\s*", seg):
        m = re.search(r"(20\d{2}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2})", raw)
        if not m:
            continue
        dt = _parse_dt(m.group(1))
        title = _clean_title(raw[:m.start()])
        rest = raw[m.end():].strip(" ·-—")
        fields = [x.strip() for x in rest.split(" · ") if x.strip()]
        status = next((x for x in fields if x in ("已发布", "待确认", "已取消", "已结束")), "")
        venue = " · ".join(x for x in fields if x != status)
        if not title:
            continue
        events.append({
            "date": dt, "date_text": m.group(1), "title": title,
            "venue": venue, "status": status, "source": "花音街", "url": url,
        })
    return events


def _extract_entity_slot(page: str, entity: str) -> dict:
    """从活动页“出演阵容”里抽这个团的上台/特典时间。"""
    plain = _plain(page)
    anchor = plain.find("出演团体")
    start_at = anchor if anchor >= 0 else 0
    candidates = [entity] + [x for x in re.split(r"[\s/｜|]+", entity) if len(x) >= 2]
    idx = -1
    for cand in candidates:
        idx = plain.find(cand, start_at)
        if idx < 0:
            idx = plain.find(cand)
        if idx >= 0:
            break
    if idx < 0:
        return {}
    window = plain[idx:idx + 320]
    times = re.findall(r"(\d{1,2}:\d{2})\s*[–\-~]\s*(\d{1,2}:\d{2})", window)
    tok_m = re.search(r"特典\s*(\d{1,2}:\d{2})\s*[–\-~]\s*(\d{1,2}:\d{2})", window)
    tokuten = f"{tok_m.group(1)}–{tok_m.group(2)}" if tok_m else ""
    if times:
        for a, b in times:
            val = f"{a}–{b}"
            if val == tokuten:
                continue
            # 跳过紧跟在“特典”后的时间（有些表格把特典放在前面）
            pos = window.find(val)
            if pos > 0 and "特典" in window[max(0, pos - 8):pos]:
                continue
            return {"slot": val, "tokuten": tokuten}
    # 单人/团体 row 可能只写一个时间，没有区间（如“午前4時 19:30”）
    single = re.search(r"(?<!\d)(\d{1,2}:\d{2})(?!\d)", window[:140])
    if single:
        return {"slot": single.group(1), "tokuten": tokuten}
    return {"tokuten": tokuten}


def _extract_event_page(url: str, page: str, entity: str = "") -> dict | None:
    plain = _plain(page)
    title_m = re.search(r"<title>(.*?)</title>", page, re.S)
    title = html.unescape(title_m.group(1)).replace("花音街｜", "").strip() if title_m else ""
    title = _clean_title(title)
    dt = None
    for key in ("演出时间", "开场时间"):
        i = plain.find(key)
        if i >= 0:
            dt = _parse_dt(plain[i:i + 120])
            if dt:
                break
    if not title or not dt:
        return None
    venue = ""
    m = re.search(r"演出场地\s+(.{2,60}?)\s+活动城市", plain)
    if m:
        venue = m.group(1).strip(" ：:·-")
    else:
        for key in ("演出场地", "活动地点"):
            i = plain.find(key)
            if i >= 0:
                venue = plain[i + len(key):i + len(key) + 60].strip(" ：:·-")
                break
    status = ""
    for s in ("已发布", "待确认", "已取消", "已结束"):
        if s in plain:
            status = s
            break
    slot = _extract_entity_slot(page, entity) if entity else {}
    return {
        "date": dt, "date_text": dt.strftime("%Y/%m/%d %H:%M"), "title": title,
        "venue": venue, "status": status, "source": "花音街", "url": url,
        "slot": slot.get("slot", ""), "tokuten": slot.get("tokuten", ""),
    }


def _title_key(title: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", _clean_title(title))[:40]


async def _fetch(url: str) -> str:
    async with httpx.AsyncClient(
        timeout=15, follow_redirects=True, headers={"User-Agent": _UA}
    ) as client:
        r = await client.get(url)
        if r.status_code != 200:
            return ""
        return r.text


async def _search_one(q: str) -> list[dict]:
    try:
        return await search.deepseek_search(q, 6, ori_question=q, purpose="search_events")
    except Exception as e:
        logger.debug(f"[EVENT] search failed q={q!r}: {type(e).__name__}: {e}")
        return []


def _dedupe(results: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in results:
        key = (r.get("url") or "") or (r.get("title") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


async def execute_search_events(entity: str, city: str = "", days: int = 7) -> str:
    entity = re.sub(r"\s+", " ", str(entity or "")).strip(" ，,。.！!？?")
    if not entity:
        return "活动检索失败：entity 为空。"
    try:
        days = max(1, min(int(days or 7), 30))
    except (TypeError, ValueError):
        days = 7
    city = str(city or "").strip()
    key = (entity, days, city)
    now = time.time()
    if key in _CACHE and now - _CACHE[key][0] < _CACHE_TTL:
        return _CACHE[key][1]

    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    queries = [
        f"{entity} 花音街",
        f"site:showstart.com {entity}",
        f"{entity} 演出 活动 {start.year}年{start.month}月",
        f"site:huayinjie.com {entity}",
    ]
    if city:
        queries.append(f"{entity} {city} 演出 活动 {start.year}年{start.month}月")

    batches = await asyncio.gather(*[_search_one(q) for q in queries])
    results = _dedupe([r for batch in batches for r in batch])

    group_urls = [r.get("url", "") for r in results if "/groups/" in (r.get("url") or "")][:2]
    if not group_urls:
        group_urls = [r.get("url", "") for r in results if "/idols/" in (r.get("url") or "")][:2]
    live_urls = [r.get("url", "") for r in results if "/lives/" in (r.get("url") or "")][:2]

    events: list[dict] = []
    for u in group_urls:
        try:
            page = await _fetch(u)
            if page:
                events.extend(_extract_group_events(u, page))
        except Exception as e:
            logger.debug(f"[EVENT] group fetch failed {u}: {e}")
    # 团体页已经给出明确活动时，不再拿活动页重复叠加（活动页标题常混导航文本）
    if not events:
        for u in live_urls:
            try:
                page = await _fetch(u)
                if page:
                    ev = _extract_event_page(u, page)
                    if ev:
                        events.append(ev)
            except Exception as e:
                logger.debug(f"[EVENT] live fetch failed {u}: {e}")

    # 团体页 meta 没写 venue/上台时间的，最多补抓 3 个活动页
    for e in events[:3]:
        if (e.get("venue") and e.get("slot")) or not e.get("url"):
            continue
        try:
            page = await _fetch(e["url"])
            if page:
                ev = _extract_event_page(e["url"], page, entity)
                if ev:
                    if not e.get("venue") and ev.get("venue"):
                        e["venue"] = ev["venue"]
                    if not e.get("status") and ev.get("status"):
                        e["status"] = ev["status"]
                    if not e.get("slot") and ev.get("slot"):
                        e["slot"] = ev["slot"]
                    if not e.get("tokuten") and ev.get("tokuten"):
                        e["tokuten"] = ev["tokuten"]
        except Exception as ex:
            logger.debug(f"[EVENT] venue/slot enrich failed {e.get('url')}: {ex}")

    # 去重：同一天 + 同标题 key；保留第一条（团体页优先）
    deduped, seen_ev = [], set()
    for e in events:
        e["title"] = _clean_title(e["title"])
        if not e["title"] or "搜索 投稿" in e["title"] or "正在加载页面" in e["title"]:
            continue
        k = (e["date"].date() if e["date"] else None, _title_key(e["title"]))
        if k in seen_ev:
            continue
        seen_ev.add(k)
        deduped.append(e)
    events = [e for e in deduped if _in_range(e["date"], start, end)]
    events.sort(key=lambda e: e["date"])

    # 秀动/其他摘要（只保留有明确日期且在范围内的）
    web_rows = []
    for r in results:
        url = r.get("url") or ""
        if "showstart.com" in url:
            src = "秀动"
        elif "huayinjie.com" in url:
            src = "花音街"
        elif "weibo" in url:
            src = "微博"
        elif "bilibili" in url:
            src = "B站"
        else:
            src = r.get("site_name") or "网页"
        summary = (r.get("summary") or r.get("snippet") or "").strip()
        dt = _parse_dt(f"{r.get('date') or ''} {summary}", start.year)
        if not _in_range(dt, start, end):
            continue
        web_rows.append({
            "source": src, "title": (r.get("title") or "").strip(),
            "url": url, "summary": re.sub(r"\s+", " ", summary)[:260],
            "date": dt,
        })

    lines = [
        f"## 活动检索结果（花音街 / 秀动 / 官方补搜）",
        f"实体：{entity} | 范围：{start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}"
        + (f" | 城市：{city}" if city else ""),
        "",
    ]
    if events:
        lines.append("### 明确活动（花音街团体页/活动页）")
        for e in events:
            bits = [e["date_text"], e["title"]]
            if e.get("venue"):
                bits.append(e["venue"])
            if e.get("status"):
                bits.append(e["status"])
            if e.get("slot"):
                bits.append(f"出演 {e['slot']}")
            if e.get("tokuten"):
                bits.append(f"特典 {e['tokuten']}")
            lines.append("- " + " | ".join(bits))
            if e.get("url"):
                lines.append(f"  来源: {e['url']}")
        lines.append("")
    if web_rows:
        lines.append("### 秀动/网页线索")
        for r in web_rows[:8]:
            date_s = r["date"].strftime("%Y/%m/%d") if r.get("date") else "日期未标明"
            lines.append(f"- [{r['source']}] {date_s} | {r['title']} | {r['summary']}")
            if r.get("url"):
                lines.append(f"  来源: {r['url']}")
        lines.append("")
    if not events and not web_rows:
        lines.append("未查到明确活动。")
        lines.append("")

    lines.append(
        "只使用上面明确写出的日期、团体、场馆；没有团体名的日期回答“未查到”，"
        "不要推断成别的团；本周和下周分开说。"
    )
    text = "\n".join(lines)
    _CACHE[key] = (now, text)
    logger.info(f"[EVENT] search entity={entity!r} days={days} events={len(events)} web={len(web_rows)}")
    return text
