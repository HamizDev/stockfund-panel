# ruff: noqa: RUF001 -- localized Chinese UI labels.
"""Optional, bounded US research lookups; never used by execution or market scans."""
from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx

from app import secrets_store
from app.config import settings

from .catalog import MarketDataError, normalize_symbol

PROVIDERS = {"finnhub": ("https://finnhub.io/api/v1", "X-Finnhub-Token"),
             "adanos": ("https://api.adanos.com", "X-API-Key")}
SOURCES = {"reddit": "/reddit/stocks/v1/compare", "news": "/news/stocks/v1/compare",
           "x": "/x/stocks/v1/compare", "polymarket": "/polymarket/stocks/v1/compare"}
_LOCK = threading.RLock()
_CALLS: dict[str, deque] = {name: deque() for name in PROVIDERS}
_COOLDOWN: dict[str, float] = {}


def _provider(name: str) -> str:
    if name not in PROVIDERS:
        raise ValueError("补充数据源无效")
    return name


def _key(name: str) -> str:
    return secrets_store.get_env_backed_secret(f"{_provider(name)}_api_key", f"{name.upper()}_API_KEY")


def status() -> dict:
    return {"storage": "local_json", "sources": [
        {"provider": name, "configured": bool(key), "masked": secrets_store.mask(key)}
        for name in PROVIDERS for key in [_key(name)]
    ]}


def _fingerprint(name: str) -> str:
    key = _key(name)
    if not key:
        raise MarketDataError("请先在数据源设置中填写并验证 API Key")
    return hashlib.sha256(key.encode()).hexdigest()


def _gate(name: str) -> None:
    with _LOCK:
        now = time.monotonic()
        if _COOLDOWN.get(name, 0) > now:
            raise MarketDataError("补充数据源正在冷却，请稍后重试")
        calls = _CALLS[name]
        while calls and now - calls[0] >= 60:
            calls.popleft()
        # Conservative local caps, not an assertion of the account's entitlement.
        if len(calls) >= (40 if name == "finnhub" else 10):
            raise MarketDataError("已达本机补充查询频率上限，请稍后重试")
        calls.append(now)


def _json(name: str, path: str, params: dict, *, candidate: str | None = None):
    key = candidate if candidate is not None else _key(name)
    if not key:
        raise MarketDataError("该补充数据源尚未配置")
    _gate(name)
    base, header = PROVIDERS[name]
    try:
        with (httpx.Client(timeout=httpx.Timeout(7, connect=3), follow_redirects=False) as client,
              client.stream("GET", base + path, params=params, headers={header: key}) as response):
            if response.status_code == 429:
                with _LOCK:
                    _COOLDOWN[name] = time.monotonic() + 300
                raise MarketDataError("免费数据源额度或频率受限，已停止重试；请检查官方账户额度")
            if response.status_code in (401, 403):
                raise MarketDataError("API Key 无效或当前免费方案没有此端点权限")
            if response.status_code != 200:
                raise MarketDataError("补充数据源暂不可用或未收录该标的")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 1024 * 1024:
                    raise MarketDataError("补充数据响应超过读取上限")
        payload = json.loads(data)
        if not isinstance(payload, (dict, list)) or (isinstance(payload, dict) and payload.get("error")):
            raise MarketDataError("补充数据返回无效，请核对官方账户权限")
        return payload
    except (httpx.HTTPError, ValueError, TypeError):
        raise MarketDataError("补充数据连接或解析失败，请稍后重试") from None


def _number(value, *, minimum: float | None = None, maximum: float | None = None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        return None
    return float(value)


def _text(value, maximum: int = 200) -> str:
    return value.strip()[:maximum] if isinstance(value, str) else ""


def _symbol(value: str) -> str:
    symbol = normalize_symbol("us", value)
    if symbol.startswith("^"):
        raise ValueError("该补充源仅查询美股股票或ETF，暂不支持指数")
    return symbol


def _wire_symbol(symbol: str) -> str:
    # Canonical Yahoo-style share classes use '-', Finnhub uses '.', e.g. BRK.B.
    return symbol.replace("-", ".")


def _quote(symbol: str, *, candidate: str | None = None) -> dict:
    row = _json("finnhub", "/quote", {"symbol": _wire_symbol(symbol)}, candidate=candidate)
    if not isinstance(row, dict) or (price := _number(row.get("c"), minimum=0.00000001)) is None:
        raise MarketDataError("未取得有效美股报价，请核对端点权限或标的覆盖")
    previous = _number(row.get("pc"), minimum=0.00000001)
    stamp = row.get("t")
    as_of = stamp * 1000 if (isinstance(stamp, int) and not isinstance(stamp, bool)
                             and 0 < stamp <= time.time() + 120) else None
    return {"price": price, "previous_close": previous,
            "change_pct": price / previous - 1 if previous else None, "as_of_ms": as_of}


def _sentiment(symbol: str, source: str, *, candidate: str | None = None) -> dict:
    if source not in SOURCES:
        raise ValueError("舆情来源无效")
    end = datetime.now(UTC).date()
    payload = _json("adanos", SOURCES[source], {"tickers": _wire_symbol(symbol),
                    "from": (end - timedelta(days=6)).isoformat(), "to": end.isoformat()}, candidate=candidate)
    if not isinstance(payload, dict) or not isinstance(payload.get("stocks"), list):
        raise MarketDataError("舆情数据结构无效")
    row = None
    for item in payload["stocks"][:20]:
        if not isinstance(item, dict):
            continue
        try:
            if _symbol(item.get("ticker", "")) == symbol:
                row = item
                break
        except ValueError:
            continue
    metric = "trades" if source == "polymarket" else "mentions"
    return {"market": "us", "symbol": symbol, "source": source,
            "buzz_score": _number((row or {}).get("buzz_score"), minimum=0, maximum=100),
            "bullish_pct": _number((row or {}).get("bullish_pct"), minimum=0, maximum=100),
            "mentions": _number((row or {}).get("trade_count" if metric == "trades" else "mentions"), minimum=0),
            "metric": metric, "period_days": _number(payload.get("period_days"), minimum=1, maximum=30),
            "fetched_at_ms": int(time.time() * 1000), "cached": False,
            "warnings": [] if row else ["该来源尚未收录此标的的有效舆情，缺失不代表中性"]}


def configure(name: str, value) -> dict:
    _provider(name)
    if not isinstance(value, str) or not re.fullmatch(r"[!-~]{8,512}", value.strip()):
        raise ValueError("API Key 格式无效，请粘贴完整值且不要包含空格")
    key = value.strip()
    # Only one official read is spent on verification; never invoke all sources.
    if name == "finnhub":
        _quote("AAPL", candidate=key)
    else:
        _sentiment("AAPL", "reddit", candidate=key)
    with _LOCK:
        _validate_store()
        secrets_store.save({f"{name}_api_key": key})
    return status()


def _validate_store() -> None:
    path = settings.data_dir / "user_data" / "secrets.json"
    for item in (path, path.parent):
        if item.exists() and (item.is_symlink() or getattr(item.lstat(), "st_file_attributes", 0)
                              & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise MarketDataError("本机凭据路径异常，未修改原文件")
    if path.exists():
        try:
            if path.is_symlink() or not isinstance(json.loads(path.read_text(encoding="utf-8")), dict):
                raise ValueError
        except (OSError, ValueError):
            raise MarketDataError("本机凭据文件异常，原文件已保留，未保存新 Key") from None


def remove(name: str) -> dict:
    _provider(name)
    with _LOCK:
        _validate_store()
        secrets_store.clear(f"{name}_api_key")
    return status()


def _cached(name: str, symbol: str, kind: str, ttl: int, fetch):
    from .service import _cached as shared_cache
    return shared_cache(("keyed-research", name, _fingerprint(name), symbol, kind), ttl, fetch)


def supplement(value: str) -> dict:
    symbol = _symbol(value)
    _fingerprint("finnhub")
    warnings = []
    quote, profile, news = None, None, []
    try:
        quote = _cached("finnhub", symbol, "quote", 60, lambda: _quote(symbol))
    except MarketDataError as error:
        warnings.append(str(error))
    try:
        profile = _cached("finnhub", symbol, "profile", 86400, lambda: _profile(symbol))
    except MarketDataError as error:
        warnings.append(str(error))
    try:
        news = _cached("finnhub", symbol, "news", 1800, lambda: _news(symbol))
    except MarketDataError as error:
        warnings.append(str(error))
    return {"market": "us", "symbol": symbol, "currency": "USD", "quote": quote,
            "profile": profile, "news": news, "warnings": list(dict.fromkeys(warnings)),
            "fetched_at_ms": int(time.time() * 1000)}


def _profile(symbol: str) -> dict | None:
    row = _json("finnhub", "/stock/profile2", {"symbol": _wire_symbol(symbol)})
    if row == {}:  # Some ETFs have quotes but no company profile.
        return None
    try:
        valid = isinstance(row, dict) and row.get("currency") == "USD" and _symbol(row.get("ticker", "")) == symbol
    except ValueError:
        valid = False
    if not valid:
        raise MarketDataError("公司资料的标的或币种不匹配，已停止展示")
    cap = _number(row.get("marketCapitalization"), minimum=0, maximum=1e15)
    return {"name": _text(row.get("name")), "industry": _text(row.get("finnhubIndustry")),
            "market_cap_usd": cap * 1_000_000 if cap is not None else None}


def _news(symbol: str) -> list:
    end = datetime.now(UTC).date()
    rows = _json("finnhub", "/company-news", {"symbol": _wire_symbol(symbol),
                 "from": (end - timedelta(days=6)).isoformat(), "to": end.isoformat()})
    if not isinstance(rows, list):
        raise MarketDataError("新闻数据结构无效")
    items = []
    for row in rows[:500]:
        if not isinstance(row, dict):
            continue
        url = _text(row.get("url"), 2048)
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        stamp = row.get("datetime")
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password
                or not isinstance(stamp, int) or isinstance(stamp, bool)
                or not 0 < stamp <= time.time() + 120 or not _text(row.get("headline"))):
            continue
        items.append({"headline": _text(row["headline"]), "url": url,
                      "published_at_ms": stamp * 1000, "summary": _text(row.get("summary"), 500)})
    return sorted(items, key=lambda row: row["published_at_ms"], reverse=True)[:10]


def sentiment(value: str, source: str) -> dict:
    symbol = _symbol(value)
    if source not in SOURCES:
        raise ValueError("舆情来源无效")
    return _cached("adanos", symbol, source, 3600, lambda: _sentiment(symbol, source))
