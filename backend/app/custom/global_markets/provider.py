# ruff: noqa: RUF001 -- localized Chinese market labels.
"""Yahoo public endpoints, with strict identity/unit/time-zone normalization.

No cookies, API credentials, broker data or optional third-party packages are used.
Price bars retain source OHLC (including the source's split treatment).
Dividend-adjusted adjclose is deliberately not mixed into them.
"""
from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from .analytics import daily_technicals
from .catalog import INDICES, MARKETS, MarketDataError, display_name, normalize_symbol, yahoo_symbol

INTERVALS = {"1d": "5y", "5m": "1mo", "1m": "5d"}
_YAHOO_QUERY_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_REQUEST_BUDGET_SECONDS = 8.0
_CONNECT_BUDGET_SECONDS = 4.0
_US_EXCHANGES = {"NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS", "BATS"}
_HEALTH_LOCK = threading.Lock()
_clock = time.monotonic
_failures = 0
_health_generation = 0
_cooldown_until = 0.0
_FAILOVER_HEALTH_LOCK = threading.Lock()
_FAILOVER_HEALTH = None


@dataclass(frozen=True)
class _HTTPStatus:
    """A non-retryable HTTP status; kept separate from transport health."""

    status_code: int


class _RetryableSourceError(Exception):
    """An upstream response that should be retried at the sibling host."""


def _request_gate() -> int:
    with _HEALTH_LOCK:
        remaining = _cooldown_until - _clock()
        if remaining > 0:
            raise MarketDataError(f'免费行情源暂时冷却，约{math.ceil(remaining)}秒后重试；旧缓存会保留')
        return _health_generation


def _source_failed(*, limited: bool = False):
    global _failures, _health_generation, _cooldown_until
    with _HEALTH_LOCK:
        _health_generation += 1
        _failures += 1
        if limited or _failures >= 3:
            _cooldown_until = _clock() + 300


def _source_succeeded(generation: int):
    global _failures
    with _HEALTH_LOCK:
        # An older concurrent success must not undo a newer failure/cooldown.
        if generation == _health_generation and _cooldown_until <= _clock():
            _failures = 0


def _failover_enabled() -> bool:
    """Read the per-user opt-in on demand so preference changes take effect."""
    try:
        from app.services import preferences

        current = preferences.load()
    except Exception:
        return False
    return isinstance(current, dict) and current.get("data_source_failover_enabled") is True


def _get_failover_health():
    global _FAILOVER_HEALTH
    if _FAILOVER_HEALTH is None:
        with _FAILOVER_HEALTH_LOCK:
            if _FAILOVER_HEALTH is None:
                from app.data_providers.source_health import SourceHealth

                _FAILOVER_HEALTH = SourceHealth(
                    clock=time.monotonic,
                    base_cooldown=30,
                    max_cooldown=300,
                    max_entries=128,
                )
    return _FAILOVER_HEALTH


async def _fetch_json_from_host_async(host: str, path: str, params: dict) -> dict | _HTTPStatus:
    """Read one fixed Yahoo host within its eight-second end-to-end budget."""
    content = bytearray()
    async with asyncio.timeout(_REQUEST_BUDGET_SECONDS):
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_REQUEST_BUDGET_SECONDS, connect=_CONNECT_BUDGET_SECONDS),
            follow_redirects=False,
        ) as client:
            async with client.stream(
                "GET",
                f"https://{host}{path}",
                params=params,
                headers={"User-Agent": "Mozilla/5.0 stockfund-panel"},
            ) as response:
                status = response.status_code
                if status == 404:
                    return _HTTPStatus(status)
                if status == 429 or status >= 500 or status in (401, 403):
                    raise _RetryableSourceError()
                if status != 200:
                    return _HTTPStatus(status)
                async for block in response.aiter_bytes():
                    content.extend(block)
                    if len(content) > _MAX_RESPONSE_BYTES:
                        raise ValueError("Yahoo response exceeds the read limit")
    payload = httpx.Response(200, content=bytes(content)).json()
    if not isinstance(payload, dict):
        raise ValueError("Yahoo response is not an object")
    return payload


def _fetch_json_from_host(host: str, path: str, params: dict) -> dict | _HTTPStatus:
    """Run the bounded async request from the provider's synchronous call path."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_fetch_json_from_host_async(host, path, params))
    raise RuntimeError("Yahoo provider must be called from a synchronous context")


def _json_with_failover(
    path: str,
    params: dict,
    *,
    validate: Callable[[dict], bool] | None = None,
) -> dict:
    health = _get_failover_health()

    def accept(value) -> bool:
        if isinstance(value, _HTTPStatus):
            return True
        return isinstance(value, dict) and (validate is None or bool(validate(value)))

    for host in _YAHOO_QUERY_HOSTS:
        attempt = health.attempt(
            ("yahoo_public", host),
            lambda host=host: _fetch_json_from_host(host, path, params),
            accept=accept,
        )
        if attempt.skipped or not attempt.success:
            continue
        if isinstance(attempt.value, _HTTPStatus):
            if attempt.value.status_code == 404:
                raise MarketDataError("免费数据源未收录该标的")
            raise MarketDataError("免费数据源暂不可用，或未收录该标的")
        if not isinstance(attempt.value, dict):
            raise MarketDataError("免费数据源返回格式无效")
        return attempt.value
    raise MarketDataError("免费行情域名暂不可用，请稍后重试；旧缓存会保留")


def _endpoint_json(path: str, params: dict, *, validate: Callable[[dict], bool]) -> dict:
    """Validate endpoint semantics inside each opt-in host health attempt."""
    if _failover_enabled():
        return _json_with_failover(path, params, validate=validate)
    # Keep existing two-argument _json integrations and single-host behavior.
    return _json(path, params)


def _json(path: str, params: dict) -> dict:
    if _failover_enabled():
        return _json_with_failover(path, params)

    # Keep the existing single-host health gate unchanged for users who have
    # not opted in to failover.
    generation = _request_gate()
    try:
        with (
            httpx.Client(timeout=httpx.Timeout(8, connect=4), follow_redirects=False) as client,
            client.stream("GET", "https://query1.finance.yahoo.com" + path,
                          params=params, headers={"User-Agent": "Mozilla/5.0 stockfund-panel"}) as response,
        ):
            if response.status_code == 429:
                _source_failed(limited=True)
                raise MarketDataError("免费数据源限流，请稍后再试")
            if response.status_code != 200:
                if response.status_code >= 500 or response.status_code in (401, 403):
                    _source_failed()
                raise MarketDataError("免费数据源暂不可用，或未收录该标的")
            content = bytearray()
            for block in response.iter_bytes():
                content.extend(block)
                if len(content) > 4 * 1024 * 1024:
                    raise MarketDataError("数据源响应超过读取上限")
        payload = httpx.Response(200, content=bytes(content)).json()
        if not isinstance(payload, dict):
            raise MarketDataError("数据源返回格式无效")
        _source_succeeded(generation)
        return payload
    except (httpx.HTTPError, ValueError) as error:
        _source_failed()
        raise MarketDataError("免费行情读取失败，请检查网络后重试") from error


def number(value, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    if not math.isfinite(value) or (positive and value <= 0) or (nonnegative and value < 0):
        return None
    return float(value)


def parse_chart(market: str, symbol: str, interval: str, payload: dict) -> dict:
    symbol = normalize_symbol(market, symbol)
    if interval not in INTERVALS:
        raise ValueError("图表周期无效")
    try:
        chart = payload["chart"]
        results = chart["result"]
        if chart.get("error") or not isinstance(results, list) or len(results) != 1:
            raise MarketDataError("未取得该标的的有效行情")
        result = results[0]
        meta = result["meta"]
        identity = normalize_symbol(market, meta["symbol"])
        expected = MARKETS[market]
        is_index = any(row[0] == symbol for row in INDICES[market])
        allowed_types = {'INDEX'} if is_index else {'EQUITY', 'ETF'}
        if (identity != symbol or meta.get("currency") != expected["currency"]
                or meta.get("exchangeTimezoneName") != expected["timezone"]
                or meta.get("instrumentType") not in allowed_types
                or meta.get("dataGranularity") != interval):
            raise MarketDataError("行情市场、标的、币种或周期不匹配，已停止展示")
        timestamps = result.get("timestamp") or []
        quotes = result["indicators"]["quote"]
        if not isinstance(timestamps, list) or len(timestamps) > 2500 or len(quotes) != 1:
            raise MarketDataError("行情记录数量或结构无效")
        fields = quotes[0]
        if any(not isinstance(fields.get(key), list) or len(fields[key]) != len(timestamps)
               for key in ("open", "high", "low", "close")):
            raise MarketDataError("行情价格字段不完整")
        zone = ZoneInfo(expected["timezone"])
        bars, keys, skipped = [], set(), 0
        now = time.time()
        for i, stamp in enumerate(timestamps):
            if (isinstance(stamp, bool) or not isinstance(stamp, int)
                    or not 0 < stamp <= now + 120):
                raise MarketDataError("行情时间无效")
            o, h, low, close = [number(fields[key][i], positive=True)
                                for key in ("open", "high", "low", "close")]
            if None in (o, h, low, close) or not low <= min(o, close) <= max(o, close) <= h:
                skipped += 1
                continue
            key = datetime.fromtimestamp(stamp, UTC).astimezone(zone).date().isoformat() if interval == "1d" else stamp
            if key in keys:
                raise MarketDataError("行情存在重复时间，已停止展示")
            keys.add(key)
            volumes = fields.get("volume")
            volume = number(volumes[i], nonnegative=True) if isinstance(volumes, list) and i < len(volumes) else None
            bars.append({"time": key, "open": o, "high": h, "low": low, "close": close, "volume": volume})
        bars.sort(key=lambda row: row["time"])
        if not bars:
            raise MarketDataError("该周期暂无有效K线；免费分钟历史窗口有限")
        stamp = meta.get("regularMarketTime")
        quote_at = stamp * 1000 if isinstance(stamp, int) and not isinstance(stamp, bool) and 0 < stamp <= now + 120 else None
        pct = number(meta.get("regularMarketChangePercent"))
        warnings = ["公开行情可能延迟；报价时间请以页面标注为准", "源站OHLC：拆股按源站处理，未做分红复权；含当前未结束K线"]
        if skipped:
            warnings.append(f"已跳过{skipped}条缺失或异常价格，未补造成交或价格")
        kind = 'index' if is_index else 'etf' if meta['instrumentType'] == 'ETF' else 'stock'
        return {"market": market, "symbol": symbol, 'kind': kind,
                "name": display_name(market, symbol, meta.get("longName") or meta.get("shortName") or symbol),
                "currency": expected["currency"], "timezone": expected["timezone"],
                "interval": interval, "source": "yahoo_public", "price_basis": "source_ohlc",
                "fetched_at_ms": round(now * 1000), "quote_at_ms": quote_at,
                "cached": False, "stale": False, "error": None, "warnings": warnings,
                "quote": {"price": number(meta.get("regularMarketPrice"), positive=True),
                          "change_pct": pct / 100 if pct is not None else None},
                "bars": bars if interval == "1d" else bars[-2200:]}
    except (AttributeError, KeyError, TypeError, IndexError, OverflowError, ValueError) as error:
        raise MarketDataError("数据源字段无效，未展示未经核验的行情") from error


def fetch_chart(market: str, symbol: str, interval: str) -> dict:
    if interval not in INTERVALS:
        raise ValueError("图表周期无效")
    code = yahoo_symbol(market, symbol)
    payload = _endpoint_json(
        "/v8/finance/chart/" + code,
        {"interval": interval, "range": INTERVALS[interval], "includePrePost": "false"},
        validate=lambda value: _chart_payload_valid(market, symbol, interval, value),
    )
    return parse_chart(market, symbol, interval, payload)


def _chart_payload_valid(market: str, symbol: str, interval: str, payload: dict) -> bool:
    parse_chart(market, symbol, interval, payload)
    return True


def fetch_quote(market: str, symbol: str) -> dict:
    """One bounded three-month response supplies quote and daily technicals."""
    code = yahoo_symbol(market, symbol)
    payload = _endpoint_json(
        '/v8/finance/chart/' + code,
        {'interval': '1d', 'range': '3mo', 'includePrePost': 'false'},
        validate=lambda value: _chart_payload_valid(market, symbol, '1d', value),
    )
    value = parse_chart(market, symbol, '1d', payload)
    return {key: value[key] for key in ('market', 'symbol', 'name', 'kind', 'currency', 'timezone',
                                      'source', 'fetched_at_ms', 'quote_at_ms', 'stale', 'error')} | value['quote'] | {
        'daily': daily_technicals(value['bars'])}


def search(market: str, query: str) -> list[dict]:
    query = query.strip()
    if not query:
        return []
    payload = _endpoint_json(
        "/v1/finance/search",
        {"q": query, "quotesCount": 15, "newsCount": 0},
        validate=_search_payload_valid,
    )
    rows = payload.get("quotes")
    if not isinstance(rows, list) or len(rows) > 50:
        raise MarketDataError("名称检索暂不可用，请尝试股票代码")
    items = []
    seen: set[str] = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("quoteType"), str)
                or row["quoteType"] not in {"EQUITY", "ETF"}
                or not isinstance(row.get("exchange"), str)):
            continue
        if market == "hk" and row.get("exchange") != "HKG":
            continue
        if market == "us" and row.get("exchange") not in _US_EXCHANGES:
            continue
        try:
            symbol = normalize_symbol(market, row["symbol"])
        except (ValueError, KeyError, AttributeError):
            continue
        # Upstream may return the same instrument more than once via aliases.
        if symbol in seen:
            continue
        seen.add(symbol)
        items.append({"symbol": symbol, "market": market,
                      "name": display_name(market, symbol, row.get("longname") or row.get("shortname") or symbol),
                      "kind": "etf" if row["quoteType"] == "ETF" else "stock"})
    return items


def _search_payload_valid(payload: dict) -> bool:
    rows = payload.get("quotes")
    return isinstance(rows, list) and len(rows) <= 50
