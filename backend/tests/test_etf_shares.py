from __future__ import annotations

import asyncio
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import ClassVar

import pytest
from app.custom.global_markets import etf_shares
from app.custom.global_markets import service as shared_service
from app.custom.global_markets.catalog import MarketDataError


@pytest.fixture(autouse=True)
def clear_etf_shares_cache():
    with shared_service._LOCK:
        for cache in (shared_service._CACHE, shared_service._PENDING, shared_service._FAILURES):
            for key in list(cache):
                if key and key[0] == "official-etf-shares":
                    cache.pop(key, None)
    yield
    with shared_service._LOCK:
        for cache in (shared_service._CACHE, shared_service._PENDING, shared_service._FAILURES):
            for key in list(cache):
                if key and key[0] == "official-etf-shares":
                    cache.pop(key, None)


def _sse_row(code: str, *, day: str = "2026-10-16", shares: str = "1234.56") -> dict:
    return {
        "STAT_DATE": day,
        "SEC_CODE": code,
        "SEC_NAME": "测试ETF",
        "TOT_VOL": shares,
    }


def _sse_payload(rows: list[dict], *, page: int = 1, total: int | None = None,
                 page_size: int = 2, page_count: int | None = None) -> dict:
    total = len(rows) if total is None else total
    page_count = max(1, math.ceil(total / page_size)) if page_count is None else page_count
    return {
        "result": rows,
        "pageHelp": {
            "total": total,
            "pageSize": page_size,
            "pageNo": page,
            "pageCount": page_count,
        },
    }


def test_symbol_and_date_validation_is_strict():
    for symbol in ("600000.SH", "510300.SZ", "159915.SH", "AAPL", "51030.SH"):
        with pytest.raises(ValueError):
            etf_shares.get(symbol)
    for day in ("2026-2-01", "2026-02-30", "yesterday"):
        with pytest.raises(ValueError):
            etf_shares.get("510300.SH", day)


def test_sse_snapshot_reads_target_and_preserves_10k_unit_and_source_url(monkeypatch):
    calls = []
    rows = [_sse_row("510300"), _sse_row("515880", shares="6514177.4")]

    def request(url, params, referer):
        calls.append((url, params, referer))
        return _sse_payload(rows, total=2, page_size=2000), f"{url}?STAT_DATE=2026-10-16"

    monkeypatch.setattr(etf_shares, "_request_json", request)
    result = etf_shares._sse_for_day(etf_shares.date(2026, 10, 16), "515880")
    assert result["name"] == "测试ETF"
    assert result["shares_10k"] == 6514177.4
    assert result["date"] == "2026-10-16" and result["exchange"] == "SH"
    assert result["source_url"].endswith("STAT_DATE=2026-10-16")
    assert calls[0][1]["sqlId"] == etf_shares.SSE_SQL_ID


def test_sse_fetches_each_page_and_requires_one_consistent_complete_snapshot(monkeypatch):
    pages = {
        1: _sse_payload([_sse_row("510300"), _sse_row("510500")], page=1,
                        total=3, page_size=2, page_count=2),
        2: _sse_payload([_sse_row("515880")], page=2,
                        total=3, page_size=2, page_count=2),
    }
    requested_pages = []

    def request(_url, params, _referer):
        page = params["pageHelp.pageNo"]
        requested_pages.append(page)
        return pages[page], f"https://query.sse.cn/?page={page}"

    monkeypatch.setattr(etf_shares, "_request_json", request)
    row, _ = etf_shares._sse_snapshot("2026-10-16", "515880")
    assert row["shares_10k"] == 1234.56
    assert requested_pages == [1, 2]


@pytest.mark.parametrize("payloads, message", [
    ({1: _sse_payload([_sse_row("510300")], page=1, total=3, page_size=2, page_count=2)},
     "记录数与分页信息不符"),
    ({1: _sse_payload([_sse_row("510300"), _sse_row("510300")], page=1,
                       total=2, page_size=2, page_count=1)}, "重复代码"),
    ({1: _sse_payload([_sse_row("510300", day="2026-10-15")], page=1,
                       total=1, page_size=2, page_count=1)}, "混入其他日期"),
])
def test_sse_rejects_incomplete_duplicate_or_mixed_date_rows(monkeypatch, payloads, message):
    monkeypatch.setattr(etf_shares, "_request_json",
                        lambda _url, params, _referer: (
                            payloads[params["pageHelp.pageNo"]], "https://example.invalid"
                        ))
    with pytest.raises(MarketDataError, match=message):
        etf_shares._sse_snapshot("2026-10-16", "510300")


def test_sse_empty_explicit_date_is_reported_as_not_published(monkeypatch):
    monkeypatch.setattr(etf_shares, "_request_json", lambda *_: (
        _sse_payload([], total=0, page_size=2000, page_count=0), "https://example.invalid"
    ))
    with pytest.raises(etf_shares._NoPublishedSnapshotError):
        etf_shares._sse_snapshot("2026-10-18", "515880")


def test_latest_sse_uses_source_reported_date_across_holiday_gaps(monkeypatch):
    seen = []

    def request(_url, params, _referer):
        seen.append(dict(params))
        rows = [_sse_row("515880", day="2026-10-09", shares="567.8")]
        return _sse_payload(rows, total=1, page_size=2000), "https://query.sse.cn/latest"

    monkeypatch.setattr(etf_shares, "_request_json", request)
    result = etf_shares._fetch_sse("515880.SH", None)
    assert result["date"] == "2026-10-09"
    assert result["shares_10k"] == 567.8
    assert len(seen) == 1 and "STAT_DATE" not in seen[0]


def test_latest_sse_rejects_pages_from_different_report_dates(monkeypatch):
    pages = {
        1: _sse_payload([
            _sse_row("510300", day="2026-10-16"),
            _sse_row("510500", day="2026-10-16"),
        ], page=1, total=3, page_size=2, page_count=2),
        2: _sse_payload([
            _sse_row("515880", day="2026-10-15"),
        ], page=2, total=3, page_size=2, page_count=2),
    }

    def request(_url, params, _referer):
        assert "STAT_DATE" not in params
        return pages[params["pageHelp.pageNo"]], "https://query.sse.cn/latest"

    monkeypatch.setattr(etf_shares, "_request_json", request)
    with pytest.raises(MarketDataError, match="分页期间混入其他日期"):
        etf_shares._sse_snapshot(None, "515880")


def _sz_row(code: str = "159915", *, shares: str = "2,593,645.49") -> dict:
    return {
        "sys_key": f"<a><u>{code}</u></a>",
        "jjjcurl": "<a><u>创业板ETF易方达</u></a>",
        "jjlb": "ETF",
        "dqgm": f"<a>{shares}</a>",
    }


def _sz_payload(rows: list[dict], *, day: str = "2026-10-16") -> list[dict]:
    return [
        {"metadata": {"tabkey": "tab1", "subname": day, "pageno": 1,
                       "pagecount": 1, "recordcount": len(rows)}, "data": rows},
        {"metadata": {"tabkey": "tab2", "subname": "", "pageno": 1,
                       "pagecount": 0, "recordcount": 0}, "data": []},
        {"metadata": {"tabkey": "tab3", "subname": "", "pageno": 1,
                       "pagecount": 0, "recordcount": 0}, "data": []},
    ]


def test_szse_reads_selected_etf_and_uses_source_snapshot_date(monkeypatch):
    calls = []

    def request(url, params, referer):
        calls.append((url, params, referer))
        return _sz_payload([_sz_row()]), f"{url}?txtkey1=159915"

    monkeypatch.setattr(etf_shares, "_request_json", request)
    result = etf_shares._fetch_szse("159915.SZ", None)
    assert result["date"] == "2026-10-16"
    assert result["name"] == "创业板ETF易方达"
    assert result["shares_10k"] == 2593645.49
    assert result["source"] == "深交所" and result["exchange"] == "SZ"
    assert calls[0][1]["txtkey1"] == "159915"
    assert calls[0][1]["selectJjlb"] == "ETF"


def test_szse_does_not_claim_its_latest_snapshot_is_historical(monkeypatch):
    monkeypatch.setattr(etf_shares, "_request_json", lambda *_: (
        _sz_payload([_sz_row()]), "https://fund.szse.cn/api/report/ShowReport/data"
    ))
    with pytest.raises(MarketDataError, match="无法查询历史日期"):
        etf_shares._fetch_szse("159915.SZ", etf_shares.date(2026, 10, 15))


@pytest.mark.parametrize("mutate, message", [
    (lambda payload: payload[0]["metadata"].update(recordcount=2), "完整的单只ETF记录"),
    (lambda payload: payload[0]["data"].append(_sz_row()), "完整的单只ETF记录"),
    (lambda payload: payload[0]["metadata"].update(subname="yesterday"), "快照日期字段无效"),
])
def test_szse_rejects_incomplete_or_invalid_snapshot(monkeypatch, mutate, message):
    payload = _sz_payload([_sz_row()])
    mutate(payload)
    monkeypatch.setattr(etf_shares, "_request_json", lambda *_: (
        payload, "https://fund.szse.cn/api/report/ShowReport/data"
    ))
    with pytest.raises(MarketDataError, match=message):
        etf_shares._fetch_szse("159915.SZ", None)


def test_get_caches_success_and_returns_safe_error_shape(monkeypatch):
    calls = []

    def fetch(symbol, requested):
        calls.append((symbol, requested))
        return {"symbol": symbol, "name": "通信ETF", "date": "2026-10-16",
                "shares_10k": 100.5, "exchange": "SH", "source": "上交所",
                "source_url": "https://query.sse.cn/", "fetched_at_ms": 100,
                "cached": False, "stale": False, "error": None}

    monkeypatch.setattr(etf_shares, "_fetch_sse", fetch)
    first = etf_shares.get("515880.SH")
    second = etf_shares.get("515880.SH")
    assert first["cached"] is False
    assert second["cached"] is True and second["shares_10k"] == 100.5
    assert len(calls) == 1
    assert "prev_date" not in second

    monkeypatch.setattr(etf_shares, "_fetch_szse", lambda *_: (_ for _ in ()).throw(
        MarketDataError("深交所当前 ETF 份额列表中未找到该代码")
    ))
    failed = etf_shares.get("159915.SZ", "2026-10-16")
    assert failed["error"] and failed["date"] is None
    assert failed["shares_10k"] is None and failed["stale"] is False


def test_get_singleflights_parallel_reads(monkeypatch):
    entered, release = Event(), Event()
    calls = []

    def fetch(symbol, requested):
        calls.append((symbol, requested))
        entered.set()
        assert release.wait(3)
        return {"symbol": symbol, "name": "通信ETF", "date": "2026-10-16",
                "shares_10k": 100.5, "exchange": "SH", "source": "上交所",
                "source_url": "https://query.sse.cn/", "fetched_at_ms": 100,
                "cached": False, "stale": False, "error": None}

    monkeypatch.setattr(etf_shares, "_fetch_sse", fetch)
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(etf_shares.get, "515880.SH")
        assert entered.wait(1)
        others = [executor.submit(etf_shares.get, "515880.SH") for _ in range(2)]
        release.set()
        results = [first.result(timeout=2), *(future.result(timeout=2) for future in others)]
    assert len(calls) == 1
    assert all(row["shares_10k"] == 100.5 for row in results)


def test_failed_fetch_is_cooled_down_and_stale_is_explicit(monkeypatch):
    calls = []

    def fail(*_):
        calls.append(1)
        raise MarketDataError("上交所 ETF 份额接口连接失败")

    monkeypatch.setattr(etf_shares, "_fetch_sse", fail)
    first = etf_shares.get("515880.SH")
    second = etf_shares.get("515880.SH")
    assert first["error"] and second["error"]
    assert len(calls) == 1

    key = ("official-etf-shares", "515880.SH", "latest")
    shared_service._CACHE[key] = (time.monotonic() - 2000, {
        "symbol": "515880.SH", "name": "通信ETF", "date": "2026-10-16",
        "shares_10k": 100.5, "exchange": "SH", "source": "上交所",
        "source_url": "https://query.sse.cn/", "fetched_at_ms": 100,
        "cached": False, "stale": False, "error": None,
    })
    stale = etf_shares.get("515880.SH")
    assert stale["stale"] is True and stale["cached"] is True
    assert stale["date"] == "2026-10-16" and stale["error"]


def test_request_total_deadline_cancels_a_trickling_response(monkeypatch):
    closed = {"response": False, "client": False}

    class SlowResponse:
        status_code = 200
        headers: ClassVar[dict[str, str]] = {}
        url = "https://example.invalid/slow"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            closed["response"] = True

        async def aiter_bytes(self):
            while True:
                await asyncio.sleep(0.01)
                yield b" "

    class SlowClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            closed["client"] = True

        def stream(self, *_args, **_kwargs):
            return SlowResponse()

    monkeypatch.setattr(etf_shares, "REQUEST_TOTAL_TIMEOUT_SECONDS", 0.08)
    monkeypatch.setattr(etf_shares.httpx, "AsyncClient", SlowClient)
    started = time.monotonic()
    with pytest.raises(MarketDataError, match="总时限"):
        etf_shares._request_json("https://example.invalid/slow", {}, "https://example.invalid/")
    assert time.monotonic() - started < 1.0
    assert closed == {"response": True, "client": True}


def test_fetch_budget_limits_cumulative_multi_page_reads(monkeypatch):
    page_rows = {
        1: [_sse_row("510300", day="2026-10-16"), _sse_row("510500", day="2026-10-16")],
        2: [_sse_row("515880", day="2026-10-16")],
    }
    pages = []
    closed = []

    class SlowResponse:
        status_code = 200
        headers: ClassVar[dict[str, str]] = {}

        def __init__(self, page):
            self.page = page
            self.url = f"https://query.sse.cn/?page={page}"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            closed.append((self.page, "response"))

        async def aiter_bytes(self):
            await asyncio.sleep(0.16)
            rows = page_rows[self.page]
            payload = _sse_payload(rows, page=self.page, total=3, page_size=2, page_count=2)
            yield json.dumps(payload).encode()

    class SlowClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def stream(self, _method, _url, *, params, **_kwargs):
            page = params["pageHelp.pageNo"]
            pages.append(page)
            return SlowResponse(page)

    monkeypatch.setattr(etf_shares, "FETCH_BUDGET_SECONDS", 0.24)
    monkeypatch.setattr(etf_shares.httpx, "AsyncClient", SlowClient)
    started = time.monotonic()
    result = etf_shares.get("515880.SH")
    elapsed = time.monotonic() - started
    assert pages == [1, 2]
    assert elapsed < 1.0
    assert result["shares_10k"] is None and result["error"]
    assert "20 秒总预算" in result["error"]
    assert closed == [(1, "response"), (2, "response")]
