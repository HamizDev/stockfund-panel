# ruff: noqa: RUF001 -- localized Chinese UI and prompt punctuation.
"""Bounded public flash-news adapters; no account, API key or trading access.

Endpoint/field contracts are documented by AKShare's stock_info_global_cls/em.
The adapters return publication time in Beijing time, separately from fetch time.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

CN = ZoneInfo("Asia/Shanghai")
SOURCE_LABELS = {"cls": "财联社", "eastmoney": "东财7×24"}
MAX_RESPONSE_BYTES = 4_000_000


@dataclass(frozen=True)
class NewsRecord:
    source: str
    source_id: str
    title: str
    summary: str
    published_at: str
    url: str | None


@dataclass(frozen=True)
class NewsFeed:
    source: str
    status: str
    items: list[NewsRecord]
    fetched_at: str
    reason: str | None = None


def now_iso() -> str:
    return datetime.now(CN).isoformat(timespec="seconds")


def plain(value, limit: int = 12_000) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]*>", " ", value)
    return re.sub(r"\s+", " ", unescape(value)).strip()[:limit]


def publication(value, *, epoch: bool = False) -> str | None:
    try:
        if epoch:
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                return None
            dt = datetime.fromtimestamp(float(value), CN)
        else:
            if not isinstance(value, str) or not re.search(r"\d{2}:\d{2}", value):
                return None
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=CN) if dt.tzinfo is None else dt.astimezone(CN)
        if not 2000 <= dt.year <= 2100:
            return None
        return dt.isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def parse_feed(source: str, payload: dict, fetched_at: str) -> NewsFeed:
    """Reject a broken schema/business error; a valid empty list is different."""
    if source not in SOURCE_LABELS:
        raise ValueError("Unknown news source")
    data = payload.get("data") if isinstance(payload, dict) else None
    field = "roll_data" if source == "cls" else "fastNewsList"
    rows = data.get(field) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return NewsFeed(source, "unavailable", [], fetched_at, "来源返回的数据结构不可用")
    items = []
    for row in rows[:20 if source == "cls" else 200]:
        if not isinstance(row, dict):
            continue
        title = plain(row.get("title"), 400)
        summary = plain(row.get("content") if source == "cls" else row.get("summary"))
        # Some CLS telegraphs have no separate title. Use their actual content's
        # headline/first sentence, rather than silently dropping genuine news.
        if not title and source == "cls" and summary:
            heading = re.match(r"【([^】]+)】", summary)
            title = heading.group(1) if heading else summary[:120]
        stamp = publication(row.get("ctime"), epoch=True) if source == "cls" else publication(row.get("showTime"))
        if not title or stamp is None:
            continue
        raw_id = str(row.get("id") if source == "cls" else row.get("code") or "")
        source_id = raw_id if re.fullmatch(r"\d{1,30}", raw_id) else ""
        url = None
        if source_id:
            url = (f"https://www.cls.cn/detail/{source_id}" if source == "cls"
                   else f"https://finance.eastmoney.com/a/{source_id}.html")
        items.append(NewsRecord(source, source_id, title, summary, stamp, url))
    if rows and not items:
        return NewsFeed(source, "unavailable", [], fetched_at, "缺少可核对的标题或发布时间")
    return NewsFeed(source, "ok" if items else "empty", items, fetched_at)


def endpoint(source: str) -> tuple[str, dict]:
    if source == "eastmoney":
        return "https://np-weblist.eastmoney.com/comm/web/getFastNewsList", {
            "client": "web", "biz": "web_724", "fastColumn": "102", "sortEnd": "", "pageSize": "200",
            "req_trace": str(round(time.time() * 1000)),
        }
    if source != "cls":
        raise ValueError("Unknown news source")
    params = {"app": "CailianpressWeb", "category": "", "last_time": int(time.time()),
              "os": "web", "refresh_type": "1", "rn": "20", "sv": "8.4.6"}
    # Public web request checksum, not a personal key or an authentication token.
    digest = hashlib.sha1(urlencode(params).encode()).hexdigest()
    params["sign"] = hashlib.md5(digest.encode()).hexdigest()
    return "https://www.cls.cn/v1/roll/get_roll_list", params


async def fetch_news_feed(source: str) -> NewsFeed:
    url, params = endpoint(source)
    fetched = now_iso()
    try:
        async with asyncio.timeout(20):
            async with httpx.AsyncClient(timeout=12, follow_redirects=False, headers={
                "User-Agent": "Mozilla/5.0", "Accept": "application/json",
                "Referer": "https://www.cls.cn/telegraph" if source == "cls" else "https://kuaixun.eastmoney.com/7_24.html",
            }) as client:
                async with client.stream("GET", url, params=params) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise ValueError("Oversized news response")
        return parse_feed(source, json.loads(content), fetched)
    except (httpx.HTTPError, ValueError, TimeoutError):
        # Never expose request URLs/proxy credentials or supplier response bodies.
        return NewsFeed(source, "unavailable", [], fetched, "公开快讯接口连接或解析失败，请稍后刷新")
