# ruff: noqa: RUF001 -- localized Chinese labels and punctuation.
"""Read-only official A-share ETF unit-count snapshots for one selected ETF."""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from zoneinfo import ZoneInfo

import httpx

from .catalog import MarketDataError
from .service import _cached

SSE_ETF_SHARES_URL = "https://query.sse.com.cn/commonQuery.do"
SZSE_ETF_LIST_URL = "https://fund.szse.cn/api/report/ShowReport/data"
SSE_SQL_ID = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
SOURCE_BY_EXCHANGE = {"SH": "上交所", "SZ": "深交所"}
TTL_SECONDS = 1800
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TOTAL_TIMEOUT_SECONDS = 12
FETCH_BUDGET_SECONDS = 20
MAX_SSE_PAGES = 5
REQUEST_TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=5.0, pool=3.0)
_CODE_RE = re.compile(r"^\d{6}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHARES_RE = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s*$")
_SZ_CODE_RE = re.compile(r"<u>(\d{6})</u>")
_SZ_NAME_RE = re.compile(r"<u>(.*?)</u>", re.DOTALL)
_SZ_VALUE_RE = re.compile(r">([^<>]+)</a>\s*$", re.DOTALL)
_FETCH_DEADLINE: ContextVar[float | None] = ContextVar("etf_shares_fetch_deadline", default=None)


class _NoPublishedSnapshotError(MarketDataError):
    """A valid official response confirms the requested date is not published."""


def _canonical_symbol(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError("ETF代码必须使用六位数字和.SH/.SZ后缀")
    symbol = value.strip().upper()
    match = re.fullmatch(r"(\d{6})\.(SH|SZ)", symbol)
    if not match:
        raise ValueError("ETF代码必须使用六位数字和.SH/.SZ后缀")
    code, exchange = match.groups()
    if (exchange == "SH" and not code.startswith("5")) or (
        exchange == "SZ" and not code.startswith("15")
    ):
        raise ValueError("ETF代码与交易所后缀不匹配")
    return f"{code}.{exchange}", exchange


def _requested_date(value: str | None) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ValueError("日期格式应为 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("日期格式应为 YYYY-MM-DD") from exc
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if parsed > today:
        raise ValueError("不能查询未来日期")
    return parsed


def _strict_int(value, label: str) -> int:
    if isinstance(value, bool):
        raise MarketDataError(f"ETF份额接口分页字段无效：{label}")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        number = int(value.strip())
    else:
        raise MarketDataError(f"ETF份额接口分页字段无效：{label}")
    if number < 0:
        raise MarketDataError(f"ETF份额接口分页字段无效：{label}")
    return number


def _parse_shares(value, label: str) -> float:
    if isinstance(value, bool) or value is None:
        raise MarketDataError(f"ETF份额字段无效：{label}")
    text = str(value).strip()
    if not _SHARES_RE.fullmatch(text):
        raise MarketDataError(f"ETF份额字段无效：{label}")
    try:
        amount = Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise MarketDataError(f"ETF份额字段无效：{label}") from exc
    if not amount.is_finite() or amount < 0:
        raise MarketDataError(f"ETF份额字段无效：{label}")
    result = float(amount)
    if not math.isfinite(result):
        raise MarketDataError(f"ETF份额字段无效：{label}")
    return result


async def _request_json_async(url: str, params: dict, referer: str) -> tuple[object, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": referer,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    }
    deadline = _FETCH_DEADLINE.get()
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        raise MarketDataError("ETF 份额查询超过 20 秒总预算")
    request_timeout = (
        REQUEST_TOTAL_TIMEOUT_SECONDS if remaining is None
        else min(REQUEST_TOTAL_TIMEOUT_SECONDS, remaining)
    )
    try:
        async with asyncio.timeout(request_timeout):
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT, follow_redirects=True
            ) as client:
                async with client.stream(
                    "GET", url, params=params, headers=headers
                ) as response:
                    if response.status_code != 200:
                        raise MarketDataError("交易所 ETF 份额接口暂不可用")
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            if int(content_length) > MAX_RESPONSE_BYTES:
                                raise MarketDataError("交易所 ETF 份额响应超过大小限制")
                        except ValueError:
                            raise MarketDataError("交易所 ETF 份额响应长度无效") from None
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise MarketDataError("交易所 ETF 份额响应超过大小限制")
                    try:
                        payload = json.loads(body.decode("utf-8-sig"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        raise MarketDataError("交易所 ETF 份额响应格式无效") from None
                    return payload, str(response.url)
    except MarketDataError:
        raise
    except TimeoutError:
        if deadline is not None and time.monotonic() >= deadline:
            raise MarketDataError("ETF 份额查询超过 20 秒总预算") from None
        raise MarketDataError("交易所 ETF 份额请求超过 12 秒总时限") from None
    except (httpx.HTTPError, OSError):
        raise MarketDataError("交易所 ETF 份额接口连接失败") from None


def _request_json(url: str, params: dict, referer: str) -> tuple[object, str]:
    """Run the cancellable async stream from the synchronous FastAPI worker route."""
    try:
        return asyncio.run(_request_json_async(url, params, referer))
    except MarketDataError:
        raise
    except RuntimeError as exc:
        if "asyncio.run() cannot be called" in str(exc):
            raise MarketDataError("ETF 份额同步读取不能在异步事件循环内运行") from None
        raise


def _sse_page(day: str | None, page: int) -> tuple[list[dict], dict, str]:
    params = {
        "sqlId": SSE_SQL_ID,
        "isPagination": "true",
        "pageHelp.pageSize": 10000,
        "pageHelp.pageNo": page,
        "pageHelp.beginPage": page,
        "pageHelp.cacheSize": 1,
        "pageHelp.endPage": page,
    }
    if day is not None:
        params["STAT_DATE"] = day
    payload, source_url = _request_json(
        SSE_ETF_SHARES_URL, params, "https://www.sse.com.cn/"
    )
    if not isinstance(payload, dict):
        raise MarketDataError("上交所 ETF 份额响应结构无效")
    rows = payload.get("result")
    page_help = payload.get("pageHelp")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise MarketDataError("上交所 ETF 份额响应缺少记录列表")
    if not isinstance(page_help, dict):
        raise MarketDataError("上交所 ETF 份额响应缺少分页信息")
    return rows, page_help, source_url


def _sse_snapshot(day: str | None, target_code: str) -> tuple[dict, str]:
    collected: list[dict] = []
    seen_codes: set[str] = set()
    source_url = SSE_ETF_SHARES_URL
    first_metadata: tuple[int, int, int] | None = None
    pages = 0
    expected_total = 0
    source_day: str | None = None

    while True:
        page_number = pages + 1
        rows, page_help, source_url = _sse_page(day, page_number)
        total = _strict_int(page_help.get("total"), "total")
        page_size = _strict_int(page_help.get("pageSize"), "pageSize")
        returned_page = _strict_int(page_help.get("pageNo"), "pageNo")
        page_count = _strict_int(page_help.get("pageCount"), "pageCount")
        if page_size < 1 or returned_page != page_number:
            raise MarketDataError("上交所 ETF 份额页码与分页元数据不符")
        if total == 0 and not rows and page_number == 1 and page_count in (0, 1):
            raise _NoPublishedSnapshotError(f"上交所 {day} 尚无已发布的 ETF 份额快照")
        if page_count < 1:
            raise MarketDataError("上交所 ETF 份额分页数量异常")
        expected_page_count = max(1, math.ceil(total / page_size))
        if page_count != expected_page_count or page_count > MAX_SSE_PAGES:
            raise MarketDataError("上交所 ETF 份额分页数量异常，未采用不完整结果")
        metadata = (total, page_size, page_count)
        if first_metadata is None:
            first_metadata = metadata
            expected_total = total
        elif metadata != first_metadata:
            raise MarketDataError("上交所 ETF 份额分页期间总数发生变化")
        expected_rows = min(page_size, max(total - (page_number - 1) * page_size, 0))
        if len(rows) != expected_rows:
            if total == 0 and not rows and page_number == 1:
                raise _NoPublishedSnapshotError(f"上交所 {day} 尚无已发布的 ETF 份额快照")
            raise MarketDataError("上交所 ETF 份额页记录数与分页信息不符")
        for row in rows:
            code = row.get("SEC_CODE")
            row_day = row.get("STAT_DATE")
            name = row.get("SEC_NAME")
            if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
                raise MarketDataError("上交所 ETF 份额代码字段无效")
            if code in seen_codes:
                raise MarketDataError("上交所 ETF 份额出现重复代码，未采用该快照")
            seen_codes.add(code)
            if not isinstance(row_day, str) or not _DATE_RE.fullmatch(row_day):
                raise MarketDataError("上交所 ETF 份额日期字段无效")
            try:
                parsed_row_day = date.fromisoformat(row_day)
            except ValueError:
                raise MarketDataError("上交所 ETF 份额日期字段无效") from None
            if parsed_row_day > datetime.now(ZoneInfo("Asia/Shanghai")).date():
                raise MarketDataError("上交所 ETF 份额快照日期晚于当前日期")
            if day is not None and row_day != day:
                raise MarketDataError("上交所 ETF 份额响应混入其他日期")
            if source_day is None:
                source_day = row_day
            elif source_day != row_day:
                raise MarketDataError("上交所 ETF 份额分页期间混入其他日期")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
                raise MarketDataError("上交所 ETF 份额名称字段无效")
            # Validate every row before treating the published snapshot as complete.
            _parse_shares(row.get("TOT_VOL"), code)
            if code == target_code:
                collected.append({
                    "name": name.strip(),
                    "shares_10k": _parse_shares(row.get("TOT_VOL"), code),
                })
        pages += 1
        if pages == page_count:
            break

    if len(seen_codes) != expected_total:
        raise MarketDataError("上交所 ETF 份额快照记录数不完整")
    if len(collected) != 1:
        if not collected:
            raise MarketDataError("上交所当日 ETF 份额列表中未找到该代码")
        raise MarketDataError("上交所 ETF 份额目标代码出现重复")
    if source_day is None:
        raise _NoPublishedSnapshotError("上交所尚无已发布的 ETF 份额快照")
    return {**collected[0], "date": source_day}, source_url


def _sse_for_day(day: date, target_code: str) -> dict:
    row, source_url = _sse_snapshot(day.isoformat(), target_code)
    return {
        "symbol": f"{target_code}.SH",
        "name": row["name"],
        "date": row["date"],
        "shares_10k": row["shares_10k"],
        "exchange": "SH",
        "source": SOURCE_BY_EXCHANGE["SH"],
        "source_url": source_url,
        "fetched_at_ms": round(time.time() * 1000),
        "cached": False,
        "stale": False,
        "error": None,
    }


def _sse_latest(target_code: str) -> dict:
    row, source_url = _sse_snapshot(None, target_code)
    return {
        "symbol": f"{target_code}.SH",
        "name": row["name"],
        "date": row["date"],
        "shares_10k": row["shares_10k"],
        "exchange": "SH",
        "source": SOURCE_BY_EXCHANGE["SH"],
        "source_url": source_url,
        "fetched_at_ms": round(time.time() * 1000),
        "cached": False,
        "stale": False,
        "error": None,
    }


def _fetch_sse(symbol: str, requested: date | None) -> dict:
    code = symbol[:6]
    if requested is not None:
        try:
            return _sse_for_day(requested, code)
        except _NoPublishedSnapshotError as exc:
            raise MarketDataError(str(exc)) from None
    try:
        # The SSE endpoint returns its complete latest snapshot when STAT_DATE
        # is omitted. The date on every row is validated before using it.
        return _sse_latest(code)
    except _NoPublishedSnapshotError as exc:
        raise MarketDataError(str(exc)) from None


def _extract_html_value(value: object, pattern: re.Pattern, label: str) -> str:
    if not isinstance(value, str):
        raise MarketDataError(f"深交所 ETF 份额字段无效：{label}")
    match = pattern.search(value)
    if not match:
        raise MarketDataError(f"深交所 ETF 份额字段格式改变：{label}")
    return unescape(match.group(1)).strip()


def _fetch_szse(symbol: str, requested: date | None) -> dict:
    code = symbol[:6]
    params = {
        "SHOWTYPE": "JSON",
        "CATALOGID": "1000_lf",
        "TABKEY": "tab1",
        "selectJjlb": "ETF",
        "PAGENO": 1,
        "txtkey1": code,
    }
    payload, source_url = _request_json(
        SZSE_ETF_LIST_URL, params, "https://fund.szse.cn/"
    )
    if not isinstance(payload, list) or not payload or not all(
        isinstance(item, dict) for item in payload
    ):
        raise MarketDataError("深交所 ETF 份额响应结构无效")
    matching_tables = [
        item for item in payload
        if isinstance(item.get("metadata"), dict)
        and item["metadata"].get("tabkey") == "tab1"
    ]
    if len(matching_tables) != 1:
        raise MarketDataError("深交所 ETF 份额响应缺少唯一的基金列表")
    table = matching_tables[0]
    metadata = table.get("metadata")
    rows = table.get("data")
    if not isinstance(metadata, dict) or not isinstance(rows, list):
        raise MarketDataError("深交所 ETF 份额响应缺少日期、分页信息或记录列表")
    raw_day = metadata.get("subname")
    if not isinstance(raw_day, str) or not _DATE_RE.fullmatch(raw_day):
        raise MarketDataError("深交所 ETF 份额快照日期字段无效")
    try:
        snapshot_day = date.fromisoformat(raw_day)
    except ValueError:
        raise MarketDataError("深交所 ETF 份额快照日期字段无效") from None
    if snapshot_day > datetime.now(ZoneInfo("Asia/Shanghai")).date():
        raise MarketDataError("深交所 ETF 份额快照日期晚于当前日期")
    page = _strict_int(metadata.get("pageno"), "深交所 pageno")
    page_count = _strict_int(metadata.get("pagecount"), "深交所 pagecount")
    record_count = _strict_int(metadata.get("recordcount"), "深交所 recordcount")
    if page != 1 or page_count != 1 or record_count != len(rows):
        raise MarketDataError("深交所按代码筛选未返回完整的单只ETF记录")
    if not rows:
        raise MarketDataError("深交所当前 ETF 份额列表中未找到该代码")
    if requested is not None and snapshot_day != requested:
        raise MarketDataError(
            f"深交所只提供当前快照，实际日期为 {snapshot_day.isoformat()}，无法查询历史日期"
        )

    matched: list[dict] = []
    seen_codes: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise MarketDataError("深交所 ETF 份额记录结构无效")
        row_code = _extract_html_value(row.get("sys_key"), _SZ_CODE_RE, "代码")
        if not _CODE_RE.fullmatch(row_code):
            raise MarketDataError("深交所 ETF 份额代码字段无效")
        if row_code in seen_codes:
            raise MarketDataError("深交所 ETF 份额出现重复代码")
        seen_codes.add(row_code)
        if row.get("jjlb") != "ETF":
            raise MarketDataError("深交所筛选结果包含非ETF记录")
        if row_code == code:
            name = _extract_html_value(row.get("jjjcurl"), _SZ_NAME_RE, "名称")
            if not name or len(name) > 120:
                raise MarketDataError("深交所 ETF 份额名称字段无效")
            raw_shares = _extract_html_value(row.get("dqgm"), _SZ_VALUE_RE, "当前规模")
            matched.append({"name": name, "shares_10k": _parse_shares(raw_shares, code)})
    if len(matched) != 1:
        if not matched:
            raise MarketDataError("深交所当前 ETF 份额列表中未找到该代码")
        raise MarketDataError("深交所 ETF 份额目标代码出现重复")
    return {
        "symbol": symbol,
        "name": matched[0]["name"],
        "date": snapshot_day.isoformat(),
        "shares_10k": matched[0]["shares_10k"],
        "exchange": "SZ",
        "source": SOURCE_BY_EXCHANGE["SZ"],
        "source_url": source_url,
        "fetched_at_ms": round(time.time() * 1000),
        "cached": False,
        "stale": False,
        "error": None,
    }


def _error_result(symbol: str, exchange: str, error: str) -> dict:
    return {
        "symbol": symbol,
        "name": symbol,
        "date": None,
        "shares_10k": None,
        "exchange": exchange,
        "source": SOURCE_BY_EXCHANGE[exchange],
        "source_url": SSE_ETF_SHARES_URL if exchange == "SH" else SZSE_ETF_LIST_URL,
        "fetched_at_ms": None,
        "cached": False,
        "stale": False,
        "error": error,
    }


def get(symbol: str, requested_date: str | None = None) -> dict:
    """Return the official share count (10k units) for one canonical ETF.

    SSE supports a requested historical date. Without one, its latest complete
    snapshot is requested directly and the source-reported date is retained.
    SZSE exposes only its latest snapshot and reports that date in metadata.
    Each cache-miss fetch has a shared 20-second network budget; every HTTP
    stream is independently capped at 12 seconds.
    """
    canonical, exchange = _canonical_symbol(symbol)
    parsed_date = _requested_date(requested_date)
    key_date = parsed_date.isoformat() if parsed_date is not None else "latest"
    fetch = _fetch_sse if exchange == "SH" else _fetch_szse

    def fetch_with_budget() -> dict:
        token = _FETCH_DEADLINE.set(time.monotonic() + FETCH_BUDGET_SECONDS)
        try:
            return fetch(canonical, parsed_date)
        finally:
            _FETCH_DEADLINE.reset(token)

    try:
        result = _cached(
            ("official-etf-shares", canonical, key_date),
            TTL_SECONDS,
            fetch_with_budget,
            stale=True,
        )
        return result
    except MarketDataError as exc:
        return _error_result(canonical, exchange, str(exc))
