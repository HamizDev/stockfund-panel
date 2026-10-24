# ruff: noqa: RUF001 -- localized Chinese market labels.
"""Bounded shared research cache; no disk, strategy, account, or quote-service writes."""
from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError

from . import hk_company_industry, hk_symbol_directory, provider, symbol_directory, symbol_search
from .catalog import MARKETS, MarketDataError, local_catalog, normalize_symbol

_LOCK = threading.RLock()
_CACHE: OrderedDict[tuple, tuple[float, dict | list]] = OrderedDict()
_PENDING: dict[tuple, Future] = {}
_FAILURES: OrderedDict[tuple, tuple[float, str]] = OrderedDict()
MAX_CACHE = 64
MAX_PENDING = 4
_HK_CLASSIFICATION_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hk-industry")
_HK_CLASSIFICATION_SLOTS = threading.BoundedSemaphore(4)
HK_CLASSIFICATION_WAIT_SECONDS = 1.0


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
    result = _cached(("chart", market, symbol, interval), 900 if interval == "1d" else 30,
                     lambda: provider.fetch_chart(market, symbol, interval), stale=True)
    classification = None
    if (market == "hk" and interval == "1d" and result.get("market") == "hk"
            and result.get("symbol") == symbol and result.get("currency") == "HKD"
            and result.get("kind") == "stock"):
        cached, value = hk_company_industry.cached(symbol)
        if not cached:
            classification_future = _submit_hk_classification(symbol)
            if classification_future is not None:
                try:
                    value = classification_future.result(timeout=HK_CLASSIFICATION_WAIT_SECONDS)
                except Exception:
                    value = None
        if (isinstance(value, dict) and value.get("source") == "eastmoney_hk_f10"
                and value.get("system") == "eastmoney_belong_industry"
                and isinstance(value.get("industry"), str) and value["industry"].strip()
                and isinstance(value.get("fetched_at_ms"), int)
                and not isinstance(value["fetched_at_ms"], bool)
                and value["fetched_at_ms"] > 0):
            classification = {
                "source": "eastmoney_hk_f10",
                "system": "eastmoney_belong_industry",
                "industry": value["industry"],
                "fetched_at_ms": value["fetched_at_ms"],
            }
    # Keep F10 outside the cached quote/chart object. It can only run after the
    # primary response verifies a HKD stock, and cannot invalidate valid prices.
    result["classification"] = classification
    return result


def _submit_hk_classification(symbol: str) -> Future | None:
    """Queue one bounded optional lookup after the chart verifies a HKD share."""
    if not _HK_CLASSIFICATION_SLOTS.acquire(blocking=False):
        return None
    try:
        future = _HK_CLASSIFICATION_EXECUTOR.submit(hk_company_industry.get, symbol)
    except RuntimeError:
        _HK_CLASSIFICATION_SLOTS.release()
        return None
    future.add_done_callback(lambda _: _HK_CLASSIFICATION_SLOTS.release())
    return future


def quote(market: str, symbol: str) -> dict:
    symbol = normalize_symbol(market, symbol)
    return _cached(('quote', market, symbol), 60,
                   lambda: provider.fetch_quote(market, symbol), stale=True)


def hk_directory() -> list[dict]:
    """Share one validated HKD security directory between search and snapshots."""
    return _cached(("hk-symbol-directory",), hk_symbol_directory.CACHE_SECONDS,
                   hk_symbol_directory.load_rows)


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
    hk_directory_used = False
    if not remote and market == "hk":
        try:
            directory_rows = hk_directory()
            remote = hk_symbol_directory.search(query, rows=directory_rows)
            hk_directory_used = bool(remote)
        except MarketDataError:
            warnings.append("HKEX公开证券目录暂不可用")
    if hk_directory_used:
        warnings.append("候选来自 HKEX 官方证券名单，仅含港币普通股、ETF及杠杆/反向产品；目录不提供行情")
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
