# ruff: noqa: RUF001 -- localized Chinese market labels.
"""Bounded shared research cache; no disk, strategy, account, or quote-service writes."""
from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError

from . import provider, symbol_directory, symbol_search
from .catalog import MARKETS, MarketDataError, local_catalog, normalize_symbol

_LOCK = threading.RLock()
_CACHE: OrderedDict[tuple, tuple[float, dict | list]] = OrderedDict()
_PENDING: dict[tuple, Future] = {}
_FAILURES: OrderedDict[tuple, tuple[float, str]] = OrderedDict()
MAX_CACHE = 64
MAX_PENDING = 4


def _cached(key: tuple, ttl: int, fetch, *, stale: bool = False):
    with _LOCK:
        row = _CACHE.get(key)
        if row and time.monotonic() - row[0] < ttl:
            _CACHE.move_to_end(key)
            result = copy.deepcopy(row[1])
            if isinstance(result, dict):
                result["cached"] = True
            return result
        pending = _PENDING.get(key)
        failure = _FAILURES.get(key)
        if not pending and failure and time.monotonic() - failure[0] < 15:
            return _stale_or_raise(row, stale, failure[1])
        owner = pending is None
        if owner:
            if len(_PENDING) >= MAX_PENDING:
                return _stale_or_raise(row, stale, "行情请求较多，请稍后再试")
            pending = Future()
            _PENDING[key] = pending
    if not owner:
        try:
            return copy.deepcopy(pending.result(timeout=10))
        except TimeoutError:
            return _stale_or_raise(row, stale, "行情请求尚未完成，请稍后重试")
    try:
        result = fetch()
        with _LOCK:
            _CACHE[key] = (time.monotonic(), copy.deepcopy(result))
            _CACHE.move_to_end(key)
            while len(_CACHE) > MAX_CACHE:
                _CACHE.popitem(last=False)
            _FAILURES.pop(key, None)
        pending.set_result(copy.deepcopy(result))
        return result
    except MarketDataError as error:
        with _LOCK:
            _FAILURES[key] = (time.monotonic(), str(error))
            _FAILURES.move_to_end(key)
            while len(_FAILURES) > MAX_CACHE:
                _FAILURES.popitem(last=False)
        try:
            result = _stale_or_raise(row, stale, str(error))
        except MarketDataError:
            pending.set_exception(error)
            raise
        pending.set_result(copy.deepcopy(result))
        return result
    except Exception as error:
        pending.set_exception(error)
        raise
    finally:
        with _LOCK:
            _PENDING.pop(key, None)


def _stale_or_raise(row, allowed: bool, message: str):
    if allowed and row and time.monotonic() - row[0] < 24 * 3600:
        result = copy.deepcopy(row[1])
        result.update(cached=True, stale=True, error=message)
        return result
    raise MarketDataError(message)


def chart(market: str, symbol: str, interval: str) -> dict:
    symbol = normalize_symbol(market, symbol)
    if interval not in provider.INTERVALS:
        raise ValueError("图表周期无效")
    return _cached(("chart", market, symbol, interval), 900 if interval == "1d" else 30,
                   lambda: provider.fetch_chart(market, symbol, interval), stale=True)


def quote(market: str, symbol: str) -> dict:
    symbol = normalize_symbol(market, symbol)
    return _cached(('quote', market, symbol), 60,
                   lambda: provider.fetch_quote(market, symbol), stale=True)


def catalog(market: str, query: str = "") -> dict:
    if market not in MARKETS:
        raise ValueError("请选择港股或美股市场")
    query = query.strip()
    if len(query) > 60:
        raise ValueError("搜索内容过长")
    local = local_catalog(market, query)
    warnings = []
    if not query or any(row["symbol"].casefold() == query.casefold() for row in local):
        return {"market": market, "items": local, "warnings": warnings}
    # Chinese-name shortcuts work offline; remote search availability varies by language.
    if local:
        return {"market": market, "items": local, "warnings": warnings}
    try:
        remote = _cached(("search", market, query.casefold()), 3600,
                         lambda: provider.search(market, query))
    except MarketDataError:
        remote = []
        warnings.append("Yahoo名称搜索暂不可用")
    tencent_used = False
    has_chinese = any("\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff" for char in query)
    if not remote and has_chinese:
        try:
            remote = _cached(("tencent-search", market, query.casefold()), 3600,
                             lambda: symbol_search.search(market, query))
            tencent_used = bool(remote)
        except MarketDataError:
            warnings.append("腾讯公开名称搜索暂不可用")
    if tencent_used:
        warnings.append("候选来自腾讯公开名称搜索，仅用于标的定位；行情与类别仍需由行情源核验")
    directory_used = False
    if not remote and market == "us":
        try:
            remote = symbol_directory.search(query)
            directory_used = bool(remote)
        except MarketDataError:
            if not warnings:
                warnings.append("名称检索暂不可用，可尝试英文名称或完整股票代码")
    if directory_used:
        warnings.append("候选来自 Nasdaq Trader 定期更新的证券目录；是否有行情仍需由行情源核验")
    if not remote:
        try:
            symbol = normalize_symbol(market, query)
            remote = [{"symbol": symbol, "name": "代码查询 · " + symbol, "market": market, "kind": "stock"}]
            warnings.append("代码将在读取行情时核验；该项尚未确认收录")
        except ValueError:
            pass
        if not remote and market == "us" and not warnings:
            warnings.append("Nasdaq Trader证券目录未找到匹配名称，可尝试英文名称或代码")
    return {"market": market, "items": remote[:20], "warnings": warnings}
