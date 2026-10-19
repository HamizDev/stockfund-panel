
"""Bounded anonymous name search via Tencent SmartBox; candidates are not quotes."""
from __future__ import annotations

import json
import re

import httpx

from .catalog import MARKETS, MarketDataError, normalize_symbol

SEARCH_URL = "https://smartbox.gtimg.cn/s3/"
MAX_QUERY_CHARS = 60
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_SOURCE_ROWS = 500
MAX_RESULTS = 20
REQUEST_TIMEOUT_SECONDS = 8
_US_VENUE_SUFFIXES = {"AM", "N", "NY", "O", "OQ", "PS"}
_FOREIGN_SUFFIXES = {"BJ", "HK", "KS", "KQ", "SH", "SS", "SZ"}
_HINT_RE = re.compile(r'\s*v_hint\s*=\s*("(?:\\.|[^"\\])*")\s*;?\s*\Z', re.DOTALL)


def _read_response(query: str) -> bytes:
    try:
        with httpx.Client(
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=4),
            follow_redirects=False,
        ) as client, client.stream(
            "GET",
            SEARCH_URL,
            params={"v": "2", "t": "all", "c": "1", "q": query},
            headers={"Accept": "text/plain"},
        ) as response:
            if response.status_code != 200:
                raise MarketDataError("腾讯公开名称搜索暂不可用")
            content = bytearray()
            for block in response.iter_bytes(chunk_size=64 * 1024):
                content.extend(block)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise MarketDataError("腾讯公开名称搜索响应超过读取上限")
            return bytes(content)
    except httpx.HTTPError as error:
        raise MarketDataError("腾讯公开名称搜索暂不可用") from error


def _us_symbol(raw_code: str) -> str | None:
    code = raw_code.strip().upper()
    if not code:
        return None
    if "." in code:
        base, suffix = code.rsplit(".", 1)
        if suffix in _US_VENUE_SUFFIXES:
            code = base
        elif suffix in _FOREIGN_SUFFIXES or len(suffix) != 1:
            return None
    try:
        return normalize_symbol("us", code)
    except ValueError:
        return None


def _kind(name: str, category: str) -> str:
    normalized = name.casefold()
    markers = ("etf", "基金", "杠杆", "槓桿", "2x", "2倍", "两倍", "兩倍", "leveraged", "leverage")
    return "etf" if category.strip().upper() in {"ETF", "ETP"} or any(
        marker in normalized for marker in markers
    ) else "stock"


def _clean_name(value: str) -> str:
    return "".join(char for char in value if char.isprintable()).strip()[:120]


def _parse_response(market: str, content: bytes) -> list[dict]:
    if market not in MARKETS:
        raise ValueError("请选择港股或美股市场")
    if len(content) > MAX_RESPONSE_BYTES:
        raise MarketDataError("腾讯公开名称搜索响应超过读取上限")
    try:
        text = content.decode("utf-8")
        match = _HINT_RE.fullmatch(text)
        if not match:
            raise ValueError("invalid response envelope")
        value = json.loads(match.group(1))
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise MarketDataError("腾讯公开名称搜索返回格式无效") from error
    if not isinstance(value, str):
        raise MarketDataError("腾讯公开名称搜索返回格式无效")
    if value in {"", "N"}:
        return []
    records = value.split("^")
    if len(records) > MAX_SOURCE_ROWS:
        raise MarketDataError("腾讯公开名称搜索结果过多")

    items = []
    seen: set[str] = set()
    for record in records:
        fields = record.split("~")
        if len(fields) < 3 or fields[0] != market:
            continue
        _, raw_code, raw_name, *metadata = fields
        name = _clean_name(raw_name)
        if not name:
            continue
        try:
            symbol = normalize_symbol("hk", raw_code) if market == "hk" else _us_symbol(raw_code)
        except ValueError:
            continue
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        category = metadata[-1] if metadata else ""
        items.append({"symbol": symbol, "name": name, "market": market, "kind": _kind(name, category)})
        if len(items) >= MAX_RESULTS:
            break
    return items


def search(market: str, query: str) -> list[dict]:
    """Return at most 20 candidates explicitly tagged for the selected market."""
    if market not in MARKETS:
        raise ValueError("请选择港股或美股市场")
    query = query.strip()
    if not query:
        return []
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError("搜索内容过长")
    return _parse_response(market, _read_response(query))
