from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
import pytest

from app.custom.global_markets import financials
from app.custom.global_markets.catalog import MarketDataError


@pytest.fixture(autouse=True)
def clear_financial_cache():
    with financials._LOCK:
        financials._CACHE.clear()
        financials._FAILURES.clear()
        financials._PENDING.clear()
    yield
    with financials._LOCK:
        financials._CACHE.clear()
        financials._FAILURES.clear()
        financials._PENDING.clear()


def metric_row(**overrides):
    return {
        "SECUCODE": "AAPL.O",
        "SECURITY_CODE": "AAPL",
        "REPORT_DATE": "2026-06-27 00:00:00",
        "NOTICE_DATE": "2026-07-31 00:00:00",
        "CURRENCY": "美元",
        "CURRENCY_ABBR": "USD",
        "ACCOUNTING_STANDARDS": "美国会计准则",
        "ROE_AVG": 12.5,
        "ROA": 4.25,
        "BASIC_EPS": 3.5,
        "OPERATE_INCOME": 100000,
        "OPERATE_INCOME_YOY": 8.0,
        "PARENT_HOLDER_NETPROFIT": 12000,
        "PARENT_HOLDER_NETPROFIT_YOY": -2.5,
        "GROSS_PROFIT_RATIO": 46.0,
        "NET_PROFIT_RATIO": 10.0,
        "DEBT_ASSET_RATIO": 32.0,
        "CURRENT_RATIO": 1.7,
        **overrides,
    }


def statement_row(*, table="income", item="营业收入", amount=100.0, **overrides):
    return {
        "SECUCODE": "00700.HK",
        "SECURITY_CODE": "00700",
        "REPORT_DATE": "2026-06-30 00:00:00",
        "CURRENCY": "人民币",
        "ACCOUNT_STANDARD": "国际会计准则",
        "ITEM_NAME": item,
        "AMOUNT": amount,
        **overrides,
    }


def payload(rows, *, pages=1, count=None):
    return {"success": True, "result": {"pages": pages, "count": len(rows) if count is None else count, "data": rows}}


def test_get_rejects_cross_market_symbols_and_unknown_table():
    with pytest.raises(ValueError, match="港股代码"):
        financials.get("hk", "AAPL")
    with pytest.raises(ValueError, match="美股代码"):
        financials.get("us", "00700.HK")
    with pytest.raises(ValueError, match="类型"):
        financials.get("us", "AAPL", "dividends")


@pytest.mark.parametrize(("market", "symbol", "table", "kind"), [
    ("hk", "02800.HK", "metrics", None),
    ("us", "SPY", "income", "etf"),
    ("hk", "^HSI", "balance", None),
])
def test_known_etf_and_index_skip_upstream_financial_request(monkeypatch, market, symbol, table, kind):
    monkeypatch.setattr(financials, "_fetch", lambda *_args: pytest.fail("ETF/index must not query F10"))

    result = financials.get(market, symbol, table, kind=kind)

    assert result["market"] == market and result["symbol"] == symbol
    assert result["table"] == table and result["items"] == []
    assert result["source"] == "eastmoney_f10"
    assert result["warnings"]


def test_metrics_preserve_source_currency_report_dates_values_and_units():
    result = financials._normalize([metric_row()], "us", "AAPL", "metrics", False)

    assert result["currency"] == "USD"
    item = result["items"][0]
    assert item["period_end"] == "2026-06-27"
    assert item["published_at"] == "2026-07-31"
    assert item["accounting_standard"] == "美国会计准则"
    values = {value["key"]: value for value in item["values"]}
    assert values["roe"] == {"key": "roe", "label": "ROE", "value": 12.5, "unit": "%"}
    assert values["basic_eps"]["value"] == 3.5 and values["basic_eps"]["unit"] == "USD/股"
    assert values["revenue"]["value"] == 100000 and values["revenue"]["unit"] == "USD"
    assert values["revenue_growth"]["value"] == 8.0
    assert values["net_income_growth"]["value"] == -2.5
    assert any("保留披露单位" in warning for warning in result["warnings"])


def test_metrics_reject_conflicting_duplicate_period_rows():
    with pytest.raises(MarketDataError, match="报告期的指标记录冲突"):
        financials._normalize(
            [metric_row(), metric_row(ROE_AVG=99.0)], "us", "AAPL", "metrics", False,
        )


def test_cumulative_and_single_quarter_with_same_end_date_are_kept_separate():
    rows = [statement_row(item='营业收入', amount=300., START_DATE='2026-01-01', REPORT_TYPE='累计季报'),
            statement_row(item='营业收入', amount=100., START_DATE='2026-04-01', REPORT_TYPE='单季报')]
    result = financials._normalize(rows, 'hk', '00700.HK', 'income', False)
    assert len(result['items']) == 2
    assert {item['report_type'] for item in result['items']} == {'累计季报', '单季报'}
    assert {item['values'][0]['value'] for item in result['items']} == {100., 300.}
    assert all(not any(key.startswith('_') for key in item) for item in result['items'])


def test_missing_flags_do_not_leak_from_later_report_periods():
    rows = [metric_row(ROE_AVG=None), metric_row(REPORT_DATE='2026-03-31', ROE_AVG=None)]
    result = financials._normalize(rows, 'us', 'AAPL', 'metrics', False)
    assert all('_missing' not in item for item in result['items'])


@pytest.mark.parametrize('changes', [
    {'REPORT_DATE': '2026-12-31'}, {'NOTICE_DATE': '2026-10-24'},
    {'START_DATE': '2026-07-01'},
])
def test_future_or_inconsistent_financial_dates_are_rejected(monkeypatch, changes):
    monkeypatch.setattr(financials, 'cn_today', lambda: date(2026, 10, 23))
    with pytest.raises(MarketDataError, match='日期超出有效范围'):
        financials._normalize([metric_row(**changes)], 'us', 'AAPL', 'metrics', False)


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), float("-inf"), "12.5"])
def test_metrics_reject_boolean_non_finite_or_string_values(value):
    with pytest.raises(MarketDataError, match="无效数值"):
        financials._normalize([metric_row(ROE_AVG=value)], "us", "AAPL", "metrics", False)


@pytest.mark.parametrize("bad_date", [None, "not-a-date", "2026-02-30"])
def test_invalid_source_report_period_fails_closed(bad_date):
    with pytest.raises(MarketDataError, match="报告期"):
        financials._normalize([metric_row(REPORT_DATE=bad_date)], "us", "AAPL", "metrics", False)


def test_income_statement_maps_allowlisted_items_without_inventing_announcement_date():
    rows = [
        statement_row(item="营业额", amount=400_000_000),
        statement_row(item="股东应占溢利", amount=100_000_000),
        statement_row(item="研发费用", amount=20_000_000),
    ]

    result = financials._normalize(rows, "hk", "00700.HK", "income", False)

    item = result["items"][0]
    assert item["period_end"] == "2026-06-30"
    assert item["published_at"] is None
    assert item["currency"] == "人民币"
    assert item["accounting_standard"] == "国际会计准则"
    assert item["values"] == [
        {"key": "revenue", "label": "营业额", "value": 400_000_000, "unit": "人民币"},
        {"key": "net_income", "label": "股东应占溢利", "value": 100_000_000, "unit": "人民币"},
    ]
    assert any("报告期不代表公告日期" in warning for warning in result["warnings"])
    assert any("其他科目暂未展开" in warning for warning in result["warnings"])


@pytest.mark.parametrize(("table", "names", "keys"), [
    ("balance", ["总资产", "总负债", "股东权益合计"], {"total_assets", "total_liabilities", "total_equity"}),
    ("cashflow", ["经营活动产生的现金流量净额", "投资活动产生的现金流量净额",
                  "筹资活动产生的现金流量净额"],
     {"operating_cash_flow", "investing_cash_flow", "financing_cash_flow"}),
])
def test_balance_and_cashflow_only_return_expected_common_fields(table, names, keys):
    rows = [statement_row(table=table, item=name, amount=index + 1) for index, name in enumerate(names)]
    result = financials._normalize(rows, "hk", "00700.HK", table, False)

    assert {value["key"] for value in result["items"][0]["values"]} == keys
    assert all(value["unit"] == "人民币" for value in result["items"][0]["values"])


def test_statement_missing_whitelisted_fields_are_explicit_nulls():
    result = financials._normalize(
        [statement_row(item="研发费用", amount=42)], "hk", "00700.HK", "income", False,
    )

    assert {value["key"] for value in result["items"][0]["values"]} == {"revenue", "net_income"}
    assert all(value["value"] is None for value in result["items"][0]["values"])
    assert any("缺失" in warning for warning in result["warnings"])


@pytest.mark.parametrize(("market", "symbol", "row"), [
    ("hk", "00700.HK", {"SECUCODE": "00005.HK", "SECURITY_CODE": "00005"}),
    ("hk", "00700.HK", {"SECUCODE": "00700.HK", "SECURITY_CODE": "00005"}),
    ("us", "AAPL", {"SECUCODE": "MSFT.O", "SECURITY_CODE": "MSFT"}),
    ("us", "AAPL", {"SECUCODE": "AAPL.N", "SECURITY_CODE": "AAPL"}),
    ("us", "AAPL", {"SECUCODE": "AAPL.X", "SECURITY_CODE": "AAPL"}),
])
def test_fetch_rejects_wrong_security_market_or_exchange(market, symbol, row):
    with pytest.raises(MarketDataError):
        financials._validate_identity(row, market, symbol, "AAPL.O" if symbol == "AAPL" else "")


def test_us_exact_exchange_suffix_is_supported_and_checked():
    canonical, exact = financials._normalize_request("us", "AAPL.O")
    assert (canonical, exact) == ("AAPL", "AAPL.O")
    assert financials._validate_identity({"SECUCODE": "AAPL.O", "SECURITY_CODE": "AAPL"}, "us", canonical, exact)
    with pytest.raises(MarketDataError, match="交易所"):
        financials._validate_identity({"SECUCODE": "AAPL.N", "SECURITY_CODE": "AAPL"}, "us", canonical, exact)


def test_empty_source_payload_returns_no_items_not_zero_values():
    result = financials._normalize([], "us", "AAPL", "income", False)
    assert result["items"] == [] and result["currency"] is None
    assert any("暂未提供" in warning for warning in result["warnings"])


def test_fetch_pages_paginates_until_ninth_period_proves_eighth_is_complete(monkeypatch):
    dates = [
        "2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30",
        "2025-06-30", "2025-03-31", "2024-12-31", "2024-09-30",
    ]
    pages = {
        1: [metric_row(SECUCODE="AAPL.O", REPORT_DATE=period, NOTICE_DATE=None) for period in dates[:8]],
        2: [metric_row(SECUCODE="AAPL.O", REPORT_DATE=dates[8], NOTICE_DATE=None)],
    }
    calls = []
    real_client = httpx.Client

    def client_factory(**kwargs):
        def handler(request):
            calls.append(dict(request.url.params))
            page_number = int(request.url.params["pageNumber"])
            return httpx.Response(200, json=payload(pages[page_number], pages=2, count=9))

        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(financials.httpx, "Client", client_factory)
    rows, incomplete = financials._fetch_pages("us", "AAPL", "metrics", "")

    assert len(rows) == 9 and not incomplete
    assert [call["pageNumber"] for call in calls] == ["1", "2"]
    assert all(call["reportName"] == financials.REPORTS["metrics"]["us"] for call in calls)
    assert all(call["filter"] == '(SECURITY_CODE="AAPL")' for call in calls)


def test_page_cap_warns_and_drops_unconfirmed_oldest_period(monkeypatch):
    calls = []
    real_client = httpx.Client

    def client_factory(**kwargs):
        def handler(request):
            page_number = int(request.url.params["pageNumber"])
            calls.append(page_number)
            row = metric_row(REPORT_DATE=f"2026-0{9 - page_number}-30 00:00:00")
            return httpx.Response(200, json=payload([row], pages=5, count=5))

        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(financials.httpx, "Client", client_factory)
    rows, incomplete = financials._fetch_pages("us", "AAPL", "metrics", "")
    result = financials._normalize(rows, "us", "AAPL", "metrics", incomplete)

    assert calls == [1, 2, 3, 4]
    assert incomplete is True
    assert len(result["items"]) == 3
    assert any("分页读取上限" in warning for warning in result["warnings"])


def test_fetch_rejects_oversize_invalid_and_failed_http_responses(monkeypatch):
    real_client = httpx.Client

    def client_for(response):
        return lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _request: response), **kwargs,
        )

    monkeypatch.setattr(financials.httpx, "Client", client_for(httpx.Response(403)))
    with pytest.raises(MarketDataError, match="HTTP 403"):
        financials._fetch_pages("us", "AAPL", "metrics", "")

    monkeypatch.setattr(financials.httpx, "Client", client_for(httpx.Response(200, content=b"not-json")))
    with pytest.raises(MarketDataError, match="格式无效"):
        financials._fetch_pages("us", "AAPL", "metrics", "")

    monkeypatch.setattr(financials.httpx, "Client", client_for(
        httpx.Response(200, content=b"x" * (financials.MAX_RESPONSE_BYTES + 1)),
    ))
    with pytest.raises(MarketDataError, match="读取上限"):
        financials._fetch_pages("us", "AAPL", "metrics", "")

    def timeout_handler(_request):
        raise httpx.ReadTimeout("sensitive upstream detail")

    monkeypatch.setattr(financials.httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(timeout_handler), **kwargs,
    ))
    with pytest.raises(MarketDataError, match="网络读取失败") as error:
        financials._fetch_pages("us", "AAPL", "metrics", "")
    assert "sensitive upstream detail" not in str(error.value)


def test_get_cache_is_bounded_by_symbol_table_and_returns_copies(monkeypatch):
    calls = []

    def fetch(market, symbol, table, exact_secucode=""):
        calls.append((market, symbol, table, exact_secucode))
        return financials._normalize([metric_row()], market, symbol, table, False)

    monkeypatch.setattr(financials, "_fetch", fetch)
    first = financials.get("us", "AAPL")
    first["items"][0]["values"].clear()
    second = financials.get("us", "AAPL")
    third = financials.get("us", "AAPL", "income")

    assert first["cached"] is False
    assert second["cached"] is True and second["items"][0]["values"]
    assert third["table"] == "income" and third["cached"] is False
    assert calls == [("us", "AAPL", "metrics", ""), ("us", "AAPL", "income", "")]


def test_expired_cache_is_returned_stale_after_failure_and_failure_is_short_cached(monkeypatch):
    now = [100.0]
    calls = []
    monkeypatch.setattr(financials, "_clock", lambda: now[0])

    def fetch(*args):
        calls.append(args)
        if len(calls) == 1:
            return financials._normalize([metric_row()], "us", "AAPL", "metrics", False)
        raise MarketDataError("safe upstream error")

    monkeypatch.setattr(financials, "_fetch", fetch)
    fresh = financials.get("us", "AAPL")
    now[0] += financials.CACHE_SECONDS + 1
    stale = financials.get("us", "AAPL")
    stale_again = financials.get("us", "AAPL")

    assert fresh["stale"] is False
    assert stale["cached"] is True and stale["stale"] is True
    assert stale_again["stale"] is True
    assert len(calls) == 2


def test_failed_refresh_delivers_stale_flag_to_singleflight_waiter(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(financials, "_clock", lambda: now[0])
    entered_refresh = threading.Event()
    release_refresh = threading.Event()
    calls = []

    def fetch(*args):
        calls.append(args)
        if len(calls) == 1:
            return financials._normalize([metric_row()], "us", "AAPL", "metrics", False)
        entered_refresh.set()
        assert release_refresh.wait(2)
        raise MarketDataError("safe upstream error")

    monkeypatch.setattr(financials, "_fetch", fetch)
    financials.get("us", "AAPL")
    now[0] += financials.CACHE_SECONDS + 1
    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(financials.get, "us", "AAPL")
        assert entered_refresh.wait(1)
        waiter = executor.submit(financials.get, "us", "AAPL")
        release_refresh.set()
        owner_result, waiter_result = owner.result(timeout=2), waiter.result(timeout=2)

    assert owner_result["stale"] is True
    assert waiter_result["stale"] is True
    assert owner_result["items"] == waiter_result["items"]
    assert len(calls) == 2


def test_concurrent_same_key_requests_are_singleflight(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fetch(market, symbol, table, exact_secucode=""):
        calls.append((market, symbol, table, exact_secucode))
        started.set()
        assert release.wait(2)
        return financials._normalize([metric_row()], market, symbol, table, False)

    monkeypatch.setattr(financials, "_fetch", fetch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(financials.get, "us", "AAPL")
        assert started.wait(1)
        second = executor.submit(financials.get, "us", "AAPL")
        release.set()
        a, b = first.result(timeout=2), second.result(timeout=2)

    assert a["items"] == b["items"]
    assert a["cached"] is False and b["cached"] is True
    assert len(calls) == 1
