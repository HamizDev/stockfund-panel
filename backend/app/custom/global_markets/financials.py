# ruff: noqa: RUF001 -- localized Chinese financial labels.
"""Bounded, on-demand Eastmoney F10 financial statements for HK/US stocks."""
from __future__ import annotations

import copy
import json
import math
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from concurrent.futures import Future
from datetime import date, datetime

import httpx

from app.market_time import cn_today

from . import catalog
from .catalog import MarketDataError, normalize_symbol

SOURCE = "eastmoney_f10"
URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
REPORTS = {
    "metrics": {"hk": "RPT_HKF10_FN_GMAININDICATOR", "us": "RPT_USF10_FN_GMAININDICATOR"},
    "income": {"hk": "RPT_HKF10_FN_INCOME", "us": "RPT_USF10_FN_INCOME"},
    "balance": {"hk": "RPT_HKF10_FN_BALANCE", "us": "RPT_USF10_FN_BALANCE"},
    "cashflow": {"hk": "RPT_HKSK_FN_CASHFLOW", "us": "RPT_USSK_FN_CASHFLOW"},
}
TABLES = frozenset(REPORTS)
PAGE_SIZE = 200
MAX_PAGES = 4
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_SECONDS = 15
TIMEOUT = httpx.Timeout(7.0, connect=4.0)
CACHE_SECONDS = 24 * 60 * 60
EMPTY_CACHE_SECONDS = 60
FAILURE_SECONDS = 15
MAX_CACHE = 64
MAX_PENDING = 4

_HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 stockfund-panel"}
_LOCK = threading.RLock()
_CACHE: OrderedDict[tuple[str, str, str, str], tuple[float, dict]] = OrderedDict()
_PENDING: dict[tuple[str, str, str, str], Future[tuple[dict, bool]]] = {}
_FAILURES: OrderedDict[tuple[str, str, str, str], float] = OrderedDict()
_clock = time.monotonic

# Eastmoney's GMAININDICATOR uses percentage points already (9.5 means 9.5%).
# Keep the source values unchanged and expose their units explicitly.
_METRICS = (
    ("roe", "ROE", ("ROE_AVG", "ROE"), "percent"),
    ("roa", "ROA", ("ROA",), "percent"),
    ("basic_eps", "每股收益（基本）", ("BASIC_EPS",), "per_share"),
    ("revenue", "营业收入", ("OPERATE_INCOME",), "currency"),
    ("revenue_growth", "营业收入同比", ("OPERATE_INCOME_YOY",), "percent"),
    ("net_income", "净利润", ("PARENT_HOLDER_NETPROFIT", "HOLDER_PROFIT", "NET_PROFIT"), "currency"),
    ("net_income_growth", "净利润同比", ("PARENT_HOLDER_NETPROFIT_YOY", "HOLDER_PROFIT_YOY", "NET_PROFIT_YOY"), "percent"),
    ("gross_margin", "毛利率", ("GROSS_PROFIT_RATIO",), "percent"),
    ("net_margin", "净利率", ("NET_PROFIT_RATIO",), "percent"),
    ("debt_asset_ratio", "资产负债率", ("DEBT_ASSET_RATIO",), "percent"),
    ("current_ratio", "流动比率", ("CURRENT_RATIO",), "multiple"),
)

# Source labels differ between HK and US reports. Preserve the actual source
# label in the response even when aliases share a stable display key.
_STATEMENT_ITEMS = {
    "income": {
        "revenue": (
            "营业收入", "主营收入", "主营业务收入", "营业额", "营运收入", "经营收入",
        ),
        "net_income": (
            "归属于母公司股东净利润", "归属于普通股股东净利润", "归母净利润",
            "股东应占溢利", "除税后溢利", "净利润",
        ),
    },
    "balance": {
        "total_assets": ("总资产",),
        "total_liabilities": ("总负债", "负债总额"),
        "total_equity": ("总权益", "股东权益合计", "股东权益"),
    },
    "cashflow": {
        "operating_cash_flow": (
            "经营活动产生的现金流量净额", "经营活动现金流量净额", "经营业务现金净额",
        ),
        "investing_cash_flow": (
            "投资活动产生的现金流量净额", "投资活动现金流量净额", "投资业务现金净额",
        ),
        "financing_cash_flow": (
            "筹资活动产生的现金流量净额", "融资活动产生的现金流量净额", "融资业务现金净额",
        ),
    },
}


def _clean_text(value: object, *, limit: int = 80) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit or any(unicodedata.category(char) == "Cc" for char in text):
        return None
    return text


def _normalize_request(market: str, value: str) -> tuple[str, str]:
    """Return the project symbol and optional exact Eastmoney US exchange code."""
    normalized = normalize_symbol(market, value)
    raw = value.strip().upper()
    if market == "us":
        match = re.fullmatch(r"([A-Z][A-Z0-9-]*)\.([ON])", raw)
        if match:
            normalized = normalize_symbol("us", match.group(1))
            return normalized, f"{normalized}.{match.group(2)}"
    return normalized, ""


def _known_kind(market: str, symbol: str) -> str | None:
    try:
        matches = catalog.local_catalog(market, symbol)
    except ValueError:
        return None
    return next((row.get("kind") for row in matches if row.get("symbol") == symbol), None)


def _response(
    market: str,
    symbol: str,
    table: str,
    *,
    currency: str | None = None,
    fetched_at_ms: int | None = None,
    warnings: list[str] | None = None,
    items: list[dict] | None = None,
) -> dict:
    return {
        "market": market,
        "symbol": symbol,
        "table": table,
        "currency": currency,
        "source": SOURCE,
        "fetched_at_ms": fetched_at_ms if fetched_at_ms is not None else round(time.time() * 1000),
        "cached": False,
        "stale": False,
        "warnings": list(warnings or []),
        "items": list(items or []),
    }


def _decorate(value: dict, *, cached: bool, stale: bool = False) -> dict:
    result = copy.deepcopy(value)
    result["cached"] = cached
    result["stale"] = stale
    if stale:
        warning = "本次更新失败，当前显示上次取得的财务数据"
        if warning not in result["warnings"]:
            result["warnings"].append(warning)
    return result


def _currency(row: dict) -> str | None:
    return _clean_text(row.get("CURRENCY_ABBR"), limit=32) or _clean_text(row.get("CURRENCY"), limit=32)


def _metadata_value(row: dict, fields: tuple[str, ...]) -> str | None:
    for field in fields:
        value = _clean_text(row.get(field), limit=80)
        if value:
            return value
    return None


def _source_number(value: object, *, allow_missing: bool = True) -> int | float | None:
    if value is None and allow_missing:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MarketDataError("Eastmoney 财报含无效数值，已停止展示")
    return value


def _period_end(value: object) -> str:
    if not isinstance(value, str):
        raise MarketDataError("Eastmoney 财报缺少有效报告期")
    raw = value.strip()
    try:
        parsed = date.fromisoformat(raw) if len(raw) == 10 else datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError as error:
        raise MarketDataError("Eastmoney 财报报告期格式无效") from error
    return parsed.isoformat()


def _report_scope(row: dict) -> dict:
    """Quarter-only and cumulative figures with the same end date are distinct."""
    start = _period_end(row['START_DATE']) if row.get('START_DATE') is not None else None
    published = _period_end(row['NOTICE_DATE']) if row.get('NOTICE_DATE') is not None else None
    return {'period_start': start, 'report_type': _clean_text(row.get('REPORT_TYPE')),
            'report_label': _clean_text(row.get('REPORT_TYPE_DETAILS')),
            'published_at': published}


def _report_key(row: dict, period: str) -> tuple:
    scope = _report_scope(row)
    return period, scope['period_start'], scope['report_type'], _clean_text(row.get('DATE_TYPE_CODE'))


def _validate_identity(row: dict, market: str, symbol: str, exact_secucode: str) -> str:
    secucode = _clean_text(row.get("SECUCODE"), limit=32)
    security_code = row.get("SECURITY_CODE")
    if secucode is None or isinstance(security_code, bool) or not isinstance(security_code, (str, int)):
        raise MarketDataError("Eastmoney 财报证券标识无效，已停止展示")

    if market == "hk":
        try:
            source_symbol = normalize_symbol("hk", secucode)
        except ValueError as error:
            raise MarketDataError("Eastmoney 财报证券市场不匹配，已停止展示") from error
        text_code = str(security_code).strip()
        if (source_symbol != symbol or not re.fullmatch(r"[0-9]{1,5}", text_code)
                or text_code.zfill(5) != symbol.removesuffix(".HK")):
            raise MarketDataError("Eastmoney 财报证券代码不匹配，已停止展示")
        return secucode

    # The source identifies US listings as TICKER.O (Nasdaq) or TICKER.N
    # (NYSE). Validate both that listing suffix and the underlying ticker.
    match = re.fullmatch(r"(.+)\.([A-Z])", secucode)
    if not match or match.group(2) not in {"O", "N"}:
        raise MarketDataError("Eastmoney 美股交易所代码无法核验，已停止展示")
    try:
        source_symbol = normalize_symbol("us", match.group(1))
        reported_symbol = normalize_symbol("us", str(security_code).strip())
    except ValueError as error:
        raise MarketDataError("Eastmoney 财报证券市场不匹配，已停止展示") from error
    if source_symbol != symbol or reported_symbol != symbol:
        raise MarketDataError("Eastmoney 财报证券代码不匹配，已停止展示")
    if exact_secucode and secucode != exact_secucode:
        raise MarketDataError("Eastmoney 财报交易所代码不匹配，已停止展示")
    return secucode


def _request_params(market: str, symbol: str, table: str, page: int, exact_secucode: str) -> dict[str, str]:
    filter_field = "SECUCODE" if market == "hk" or exact_secucode else "SECURITY_CODE"
    filter_value = exact_secucode or symbol
    if market == "hk":
        filter_value = symbol
    return {
        "reportName": REPORTS[table][market],
        "columns": "ALL",
        "quoteColumns": "",
        "pageNumber": str(page),
        "pageSize": str(PAGE_SIZE),
        "sortTypes": "-1",
        "sortColumns": "REPORT_DATE",
        "source": "F10",
        "client": "PC",
        "filter": f'({filter_field}="{filter_value}")',
    }


def _fetch_pages(market: str, symbol: str, table: str, exact_secucode: str) -> tuple[list[dict], bool]:
    """Fetch enough descending rows to finish the latest eight report periods."""
    started = _clock()
    rows: list[dict] = []
    period_order: list[str] = []
    seen_periods: set[str] = set()
    expected_pages: int | None = None
    crossed_eighth = False
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers=_HEADERS) as client:
            for page in range(1, MAX_PAGES + 1):
                remaining = MAX_SECONDS - (_clock() - started)
                if remaining <= 0:
                    raise MarketDataError("Eastmoney 港美股财报读取超时")
                params = _request_params(market, symbol, table, page, exact_secucode)
                request_timeout = httpx.Timeout(
                    min(TIMEOUT.read, remaining),
                    connect=min(TIMEOUT.connect, remaining),
                )
                with client.stream("GET", URL, params=params, timeout=request_timeout) as response:
                    if response.status_code != 200:
                        raise MarketDataError(f"Eastmoney 港美股财报读取失败 (HTTP {response.status_code})")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise MarketDataError("Eastmoney 港美股财报响应超过读取上限")
                        if _clock() - started > MAX_SECONDS:
                            raise MarketDataError("Eastmoney 港美股财报读取超时")
                try:
                    payload = json.loads(body)
                except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as error:
                    raise MarketDataError("Eastmoney 港美股财报响应格式无效") from error
                if not isinstance(payload, dict) or payload.get("success") is not True:
                    raise MarketDataError("Eastmoney 港美股财报响应结构无效")
                result = payload.get("result")
                if not isinstance(result, dict):
                    raise MarketDataError("Eastmoney 港美股财报响应结构无效")
                page_rows = result.get("data")
                if not isinstance(page_rows, list) or len(page_rows) > PAGE_SIZE:
                    raise MarketDataError("Eastmoney 港美股财报分页结构无效")
                page_total = result.get("pages")
                count = result.get("count")
                if (isinstance(page_total, bool) or not isinstance(page_total, int) or page_total < 0
                        or isinstance(count, bool) or not isinstance(count, int) or count < 0):
                    raise MarketDataError("Eastmoney 港美股财报分页信息无效")
                if expected_pages is None:
                    expected_pages = page_total
                    if count == 0 and page_rows:
                        raise MarketDataError("Eastmoney 港美股财报分页总数不一致")
                    if count > 0 and not page_rows:
                        raise MarketDataError("Eastmoney 港美股财报分页结果不完整")
                elif page_total != expected_pages:
                    raise MarketDataError("Eastmoney 港美股财报分页期间发生变化")

                if not page_rows:
                    if page <= (expected_pages or 0) and count:
                        raise MarketDataError('港美股财报缺少应有分页，已停止展示')
                    break
                for row in page_rows:
                    if not isinstance(row, dict):
                        raise MarketDataError("Eastmoney 港美股财报记录结构无效")
                    _validate_identity(row, market, symbol, exact_secucode)
                    period = _period_end(row.get("REPORT_DATE"))
                    if period not in seen_periods:
                        if period_order and period > period_order[-1]:
                            raise MarketDataError("Eastmoney 港美股财报报告期顺序无效")
                        seen_periods.add(period)
                        period_order.append(period)
                    rows.append(row)
                if len(period_order) > 8:
                    crossed_eighth = True
                    break
                if page >= expected_pages:
                    break
    except MarketDataError:
        raise
    except httpx.HTTPError as error:
        raise MarketDataError("Eastmoney 港美股财报网络读取失败") from error

    incomplete = bool(expected_pages and page >= MAX_PAGES and page < expected_pages and not crossed_eighth)
    return rows, incomplete


def _metric_unit(unit_kind: str, currency: str | None) -> str | None:
    if unit_kind == "percent":
        return "%"
    if unit_kind == "multiple":
        return "倍"
    if unit_kind == "currency":
        return currency
    if unit_kind == "per_share":
        return f"{currency}/股" if currency else "每股（源货币见报告期）"
    return None


def _normalize_metrics(rows: list[dict], market: str, symbol: str, incomplete: bool) -> dict:
    warning = ["展示常用财务指标；比率和金额保留披露单位，不同币种应分别比较"]
    if not rows:
        warning.append("数据源暂未提供该标的此类报表数据")
        return _response(market, symbol, "metrics", warnings=warning)

    grouped: OrderedDict[tuple, dict] = OrderedDict()
    for row in rows:
        period = _period_end(row.get("REPORT_DATE"))
        currency = _currency(row)
        accounting = _metadata_value(row, ("ACCOUNT_STANDARD", "ACCOUNTING_STANDARDS"))
        scope = _report_scope(row)
        values = []
        missing = False
        for key, label, source_fields, unit_kind in _METRICS:
            source_field = next((field for field in source_fields if field in row), None)
            if source_field is None:
                missing = True
                value = None
            else:
                value = _source_number(row.get(source_field))
                if value is None:
                    missing = True
            values.append({"key": key, "label": label, "value": value,
                           "unit": _metric_unit(unit_kind, currency)})
        item = {
            "period_end": period,
            **scope,
            "currency": currency,
            "accounting_standard": accounting,
            "values": values,
            "_missing": missing,
        }
        group_key = _report_key(row, period)
        previous = grouped.get(group_key)
        if previous is not None and previous != item:
            raise MarketDataError("Eastmoney 同一报告期的指标记录冲突，已停止展示")
        grouped[group_key] = item

    items = list(grouped.values())[:8]
    missing_flags = [item.pop('_missing') for item in items]
    if any(missing_flags):
        warning.append("部分常用指标缺少源字段或数值，已留空，未以零补齐")
    if any(item["published_at"] is None for item in items):
        warning.append("部分报告期未提供公告日期；报告期不代表公告日期")
    if incomplete:
        warning.append("上游数据超过分页读取上限，已丢弃可能不完整的最旧报告期")
    currencies = {item["currency"] for item in items if item["currency"]}
    if len(currencies) > 1:
        warning.append("不同报告期的披露币种可能不同，请按各期币种分别比较")
    return _response(market, symbol, "metrics", currency=items[0]["currency"] if items else None,
                     warnings=warning, items=items)


def _source_item_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return unicodedata.normalize("NFKC", value).strip()


def _statement_key(table: str, label: str) -> str | None:
    for key, names in _STATEMENT_ITEMS[table].items():
        if label in names:
            return key
    return None


def _normalize_statement(rows: list[dict], market: str, symbol: str, table: str, incomplete: bool) -> dict:
    warning = ["展示常用财务科目，其他科目暂未展开；金额保留报表币种"]
    if not rows:
        warning.append("数据源暂未提供该标的此类报表数据")
        return _response(market, symbol, table, warnings=warning)

    aliases = _STATEMENT_ITEMS[table]
    priorities = {key: {label: index for index, label in enumerate(labels)}
                  for key, labels in aliases.items()}
    grouped: OrderedDict[tuple, dict] = OrderedDict()
    for row in rows:
        period = _period_end(row.get("REPORT_DATE"))
        currency = _currency(row)
        accounting = _metadata_value(row, ("ACCOUNT_STANDARD", "ACCOUNTING_STANDARDS"))
        scope = _report_scope(row)
        published_at = scope['published_at']
        group = grouped.setdefault(_report_key(row, period), {
            "period_end": period,
            **scope,
            "currency": currency,
            "accounting_standard": accounting,
            "_values": {},
            "_priorities": {},
            "_missing": False,
        })
        for field, value in (("currency", currency), ("accounting_standard", accounting),
                             ("published_at", published_at)):
            if value is not None and group[field] is not None and value != group[field]:
                raise MarketDataError("Eastmoney 同一报告期的币种或会计口径冲突，已停止展示")
            if group[field] is None and value is not None:
                group[field] = value

        source_label = _source_item_text(row.get("ITEM_NAME"))
        key = _statement_key(table, source_label) if source_label else None
        if key is None:
            continue
        amount = _source_number(row.get("AMOUNT"))
        priority = priorities[key][source_label]
        previous_priority = group["_priorities"].get(key)
        if previous_priority is None or priority < previous_priority:
            group["_values"][key] = {"key": key, "label": source_label, "value": amount,
                                     "unit": currency}
            group["_priorities"][key] = priority
        elif priority == previous_priority:
            previous_value = group["_values"][key]["value"]
            if previous_value != amount:
                raise MarketDataError("Eastmoney 同一报告期的报表科目冲突，已停止展示")
        if amount is None:
            group["_missing"] = True

    items = []
    for group in list(grouped.values())[:8]:
        source_values = group.pop("_values")
        group.pop("_priorities", None)
        missing_value = group.pop("_missing")
        values = [
            source_values.get(key, {
                "key": key,
                "label": aliases[key][0],
                "value": None,
                "unit": group["currency"],
            })
            for key in aliases
        ]
        if any(value["value"] is None for value in values):
            missing_value = True
        if missing_value:
            group["_missing"] = True
        group["values"] = values
        items.append(group)

    missing_flags = [item.pop('_missing', False) for item in items]
    if any(missing_flags):
        warning.append("部分常用科目或数值缺失，已省略或留空，未以零补齐")
    if any(item["published_at"] is None for item in items):
        warning.append("数据源未提供公告日期；报告期不代表公告日期")
    if any(item["currency"] is None for item in items):
        warning.append("部分报告期未提供货币单位，相关值没有自行换算")
    if incomplete:
        warning.append("上游数据超过分页读取上限，已丢弃可能不完整的最旧报告期")
    currencies = {item["currency"] for item in items if item["currency"]}
    if len(currencies) > 1:
        warning.append("不同报告期的披露币种可能不同，请按各期币种分别比较")
    return _response(market, symbol, table, currency=items[0]["currency"] if items else None,
                     warnings=warning, items=items)


def _normalize(rows: list[dict], market: str, symbol: str, table: str, incomplete: bool) -> dict:
    cutoff = cn_today().isoformat()
    for row in rows:
        period = _period_end(row.get('REPORT_DATE'))
        scope = _report_scope(row)
        if (period > cutoff or (scope['published_at'] and scope['published_at'] > cutoff)
                or (scope['period_start'] and scope['period_start'] > period)):
            raise MarketDataError('财务报告日期超出有效范围，已停止展示')
    if incomplete and rows:
        oldest = min(_period_end(row.get('REPORT_DATE')) for row in rows)
        rows = [row for row in rows if _period_end(row.get('REPORT_DATE')) != oldest]
    if table == "metrics":
        return _normalize_metrics(rows, market, symbol, incomplete)
    return _normalize_statement(rows, market, symbol, table, incomplete)


def _fetch(market: str, symbol: str, table: str, exact_secucode: str = "") -> dict:
    rows, incomplete = _fetch_pages(market, symbol, table, exact_secucode)
    return _normalize(rows, market, symbol, table, incomplete)


def _cache_key(market: str, symbol: str, table: str, exact_secucode: str) -> tuple[str, str, str, str]:
    return market, symbol, table, exact_secucode


def _cache_value(key: tuple[str, str, str, str]) -> tuple[bool, dict | None, bool]:
    entry = _CACHE.get(key)
    if entry is not None:
        age = _clock() - entry[0]
        ttl = EMPTY_CACHE_SECONDS if not entry[1]["items"] else CACHE_SECONDS
        _CACHE.move_to_end(key)
        if age < ttl:
            return True, copy.deepcopy(entry[1]), False
    failed_at = _FAILURES.get(key)
    if failed_at is not None:
        if _clock() - failed_at < FAILURE_SECONDS:
            _FAILURES.move_to_end(key)
            if entry is not None:
                return True, copy.deepcopy(entry[1]), True
            return True, None, False
        _FAILURES.pop(key, None)
    return False, copy.deepcopy(entry[1]) if entry is not None else None, False


def _remember(key: tuple[str, str, str, str], value: dict) -> None:
    with _LOCK:
        _CACHE[key] = (_clock(), copy.deepcopy(value))
        _CACHE.move_to_end(key)
        while len(_CACHE) > MAX_CACHE:
            _CACHE.popitem(last=False)
        _FAILURES.pop(key, None)


def _remember_failure(key: tuple[str, str, str, str]) -> None:
    with _LOCK:
        _FAILURES[key] = _clock()
        _FAILURES.move_to_end(key)
        while len(_FAILURES) > MAX_CACHE:
            _FAILURES.popitem(last=False)


def _not_supported(market: str, symbol: str, table: str, kind: str) -> dict:
    label = "指数没有公司财务报表" if kind == "index" else "ETF 不适用公司财务报表"
    return _response(market, symbol, table, warnings=[label])


def get(market: str, symbol: str, table: str = "metrics", *, kind: str | None = None) -> dict:
    """Read one HK/US stock's recent F10 table on demand.

    ``kind`` is optional and lets a caller identify an unlisted ETF/index before
    requesting. Known catalog ETFs and indices are skipped even without it.
    """
    if table not in TABLES:
        raise ValueError("财务报表类型无效")
    normalized, exact_secucode = _normalize_request(market, symbol)
    if kind is not None and kind not in {"stock", "etf", "index"}:
        raise ValueError("证券类型无效")
    known = _known_kind(market, normalized)
    effective_kind = known if known in {"etf", "index"} else (kind or known)
    if effective_kind in {"etf", "index"}:
        return _not_supported(market, normalized, table, effective_kind)

    key = _cache_key(market, normalized, table, exact_secucode)
    with _LOCK:
        hit, cached_value, stale = _cache_value(key)
        if hit:
            if cached_value is None:
                raise MarketDataError("港美股财务数据暂不可用，请稍后重试")
            return _decorate(cached_value, cached=True, stale=stale)
        pending = _PENDING.get(key)
        owner = pending is None
        if owner:
            if len(_PENDING) >= MAX_PENDING:
                raise MarketDataError("当前财务数据请求较多，请稍后重试")
            pending = Future()
            _PENDING[key] = pending

    if not owner:
        try:
            value, stale = pending.result(timeout=MAX_SECONDS + 2)
        except TimeoutError as error:
            raise MarketDataError("港美股财务数据读取超时") from error
        return _decorate(value, cached=True, stale=stale)

    result: dict | None = None
    failure: MarketDataError | None = None
    try:
        result = _fetch(market, normalized, table, exact_secucode)
        result["fetched_at_ms"] = round(time.time() * 1000)
        _remember(key, result)
    except MarketDataError as error:
        failure = error
        _remember_failure(key)
    except Exception as error:
        failure = MarketDataError("港美股财务数据读取失败，请稍后重试")
        _remember_failure(key)
        # Keep unexpected exception details out of callers and user-visible logs.
        del error
    finally:
        with _LOCK:
            _, old_value, _ = _cache_value(key)
            _PENDING.pop(key, None)
            if failure is not None and old_value is not None:
                result = old_value
            if result is None:
                pending.set_exception(failure or MarketDataError("港美股财务数据读取失败，请稍后重试"))
            else:
                pending.set_result((copy.deepcopy(result), failure is not None))

    if failure is not None:
        if result is not None:
            return _decorate(result, cached=True, stale=True)
        raise failure
    assert result is not None
    return _decorate(result, cached=False)
