"""Offline contract, mapping, date-safety, and paging tests for Eastmoney financials."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import polars as pl
import pytest
import yaml

from app.plugins.eastmoney_financial.client import (
    MAX_PAGES_PER_PERIOD,
    PAGE_SIZE,
    REQUEST_INTERVAL_S,
    USER_AGENT,
    EastmoneyClient,
    EastmoneyError,
)
from app.plugins.eastmoney_financial.provider import (
    EastmoneyFinancialProvider,
    merge_metrics_with_balance,
)


class _FakeFinancialClient:
    def __init__(self, reports=None, errors=None):
        self.reports = reports or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, str]] = []

    def fetch_period(self, table, period):
        self.calls.append((table, str(period)))
        error = self.errors.get((table, str(period)))
        if error:
            raise error
        return [dict(row) for row in self.reports.get((table, str(period)), [])]


@pytest.fixture
def as_of(monkeypatch):
    monkeypatch.setattr(
        "app.plugins.eastmoney_financial.provider.cn_today",
        lambda: date(2026, 10, 2),
    )


def _provider(client):
    return EastmoneyFinancialProvider(client=client)


def _statement_row(table, *, period="2026-06-30", symbol="600519.SH", notice="2026-08-15", **values):
    return {
        "SECUCODE": symbol,
        "REPORT_DATE": f"{period} 00:00:00",
        "NOTICE_DATE": notice,
        **values,
    }


def _metrics_row(
    *,
    period="2026-06-30",
    symbol="600519.SH",
    notice="2026-08-15",
    update=None,
    security_type="058001001",
    isnew="1",
    **values,
):
    return {
        "SECUCODE": symbol,
        "REPORTDATE": f"{period} 00:00:00",
        "NOTICE_DATE": notice,
        "UPDATE_DATE": update,
        "SECURITY_TYPE_CODE": security_type,
        "ISNEW": isnew,
        **values,
    }


def _payload(rows, *, pages=1, count=None):
    return {
        "success": True,
        "code": 0,
        "result": {
            "count": len(rows) if count is None else count,
            "pages": pages,
            "data": rows,
        },
    }


def test_plugin_manifest_only_advertises_financial_dataset():
    manifest_path = (
        Path(__file__).parents[1]
        / "app"
        / "plugins"
        / "eastmoney_financial"
        / "plugin.yaml"
    )
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["name"] == "eastmoney_financial"
    assert manifest["datasets"] == ["financial"]
    assert manifest["entry"] == (
        "app.plugins.eastmoney_financial.provider:EastmoneyFinancialProvider"
    )
    assert "api_key_env" not in manifest


def test_client_uses_verified_statement_and_metrics_filters_and_paces_pages():
    requests = []
    sleeps = []

    def handler(request):
        params = dict(request.url.params)
        requests.append((params, request.headers.get("user-agent")))
        page_number = int(params["pageNumber"])
        row_count = PAGE_SIZE if page_number == 1 else 1
        return httpx.Response(
            200,
            json=_payload(
                [{"SECUCODE": f"page-{page_number}"}] * row_count,
                pages=2,
                count=PAGE_SIZE + 1,
            ),
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EastmoneyClient(http_client=http, sleep=sleeps.append)
    try:
        client.fetch_period("income", "2026-06-30")
        client.fetch_period("metrics", "2026-06-30")
    finally:
        http.close()

    assert len(requests) == 4
    assert [params["reportName"] for params, _ in requests] == [
        "RPT_DMSK_FN_INCOME",
        "RPT_DMSK_FN_INCOME",
        "RPT_LICO_FN_CPD",
        "RPT_LICO_FN_CPD",
    ]
    assert [params["pageNumber"] for params, _ in requests] == ["1", "2", "1", "2"]
    for params, user_agent in requests:
        assert params["columns"] == "ALL"
        assert params["pageSize"] == str(PAGE_SIZE)
        assert params["sortColumns"] == "SECURITY_CODE"
        assert params["sortTypes"] == "1"
        assert user_agent == USER_AGENT
    assert requests[0][0]["filter"] == "(REPORT_DATE='2026-06-30')"
    assert requests[2][0]["filter"] == (
        '(REPORTDATE=\'2026-06-30\')(SECURITY_TYPE_CODE in '
        '("058001001","058001008"))'
    )
    assert sleeps == [REQUEST_INTERVAL_S] * 3


def test_client_treats_verified_9201_empty_period_as_no_rows():
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"success": False, "code": 9201, "message": "返回数据为空", "result": None},
            )
        )
    )
    client = EastmoneyClient(http_client=http, sleep=lambda _: None)
    try:
        assert client.fetch_period("income", "2026-09-30") == []
    finally:
        http.close()


def test_client_raises_on_page_failure_without_returning_partial_rows():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json=_payload(
                    [{"SECUCODE": "600519.SH"}] * PAGE_SIZE,
                    pages=2,
                    count=PAGE_SIZE + 1,
                ),
            )
        return httpx.Response(200, json={"success": False, "code": 5001, "result": None})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EastmoneyClient(http_client=http, sleep=lambda _: None)
    try:
        with pytest.raises(EastmoneyError, match="rejected"):
            client.fetch_period("income", "2026-06-30")
    finally:
        http.close()
    assert calls == 2


def test_client_enforces_page_cap_and_pagination_consistency():
    too_many_http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload(
                    [{"SECUCODE": "600519.SH"}] * PAGE_SIZE,
                    pages=MAX_PAGES_PER_PERIOD + 1,
                    count=PAGE_SIZE * (MAX_PAGES_PER_PERIOD + 1),
                ),
            )
        )
    )
    try:
        with pytest.raises(EastmoneyError, match="limit"):
            EastmoneyClient(http_client=too_many_http).fetch_period("income", "2026-06-30")
    finally:
        too_many_http.close()

    fractional_http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload([{"SECUCODE": "600519.SH"}], pages=1.5, count=1),
            )
        )
    )
    try:
        with pytest.raises(EastmoneyError, match="invalid page count"):
            EastmoneyClient(http_client=fractional_http).fetch_period(
                "income", "2026-06-30"
            )
    finally:
        fractional_http.close()

    page = 0

    def changing_pages(request):
        nonlocal page
        page += 1
        return httpx.Response(
            200,
            json=_payload(
                [{"SECUCODE": "600519.SH"}] * (PAGE_SIZE if page == 1 else 1),
                pages=2 if page == 1 else 3,
                count=PAGE_SIZE + 1,
            ),
        )

    changing_http = httpx.Client(transport=httpx.MockTransport(changing_pages))
    try:
        with pytest.raises(EastmoneyError, match="pagination changed"):
            EastmoneyClient(http_client=changing_http, sleep=lambda _: None).fetch_period(
                "income", "2026-06-30"
            )
    finally:
        changing_http.close()


def test_client_rejects_empty_pages_count_changes_and_incomplete_counts():
    page_number = 0

    def empty_second_page(request):
        nonlocal page_number
        page_number += 1
        return httpx.Response(
            200,
            json=_payload(
                [{"SECUCODE": "600519.SH"}] * (PAGE_SIZE if page_number == 1 else 0),
                pages=2,
                count=PAGE_SIZE + 1,
            ),
        )

    empty_http = httpx.Client(transport=httpx.MockTransport(empty_second_page))
    try:
        with pytest.raises(EastmoneyError, match="incomplete page"):
            EastmoneyClient(http_client=empty_http, sleep=lambda _: None).fetch_period(
                "income", "2026-06-30"
            )
    finally:
        empty_http.close()

    page_number = 0

    def changing_count(request):
        nonlocal page_number
        page_number += 1
        return httpx.Response(
            200,
            json=_payload(
                [{"SECUCODE": "600519.SH"}] * (PAGE_SIZE if page_number == 1 else 2),
                pages=2,
                count=PAGE_SIZE + (1 if page_number == 1 else 2),
            ),
        )

    count_http = httpx.Client(transport=httpx.MockTransport(changing_count))
    try:
        with pytest.raises(EastmoneyError, match="count changed"):
            EastmoneyClient(http_client=count_http, sleep=lambda _: None).fetch_period(
                "income", "2026-06-30"
            )
    finally:
        count_http.close()

    inconsistent_http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload([{"SECUCODE": "600519.SH"}] * PAGE_SIZE, pages=2, count=PAGE_SIZE),
            )
        )
    )
    try:
        with pytest.raises(EastmoneyError, match="count/pages mismatch"):
            EastmoneyClient(http_client=inconsistent_http).fetch_period("income", "2026-06-30")
    finally:
        inconsistent_http.close()


def test_statement_mappings_keep_attributable_income_separate_and_numbers_finite(as_of):
    client = _FakeFinancialClient(
        reports={
            (
                "income",
                "2026-06-30",
            ): [
                _statement_row(
                    "income",
                    TOTAL_OPERATE_INCOME="1234.5",
                    OPERATE_COST="500",
                    SALE_EXPENSE=10,
                    MANAGE_EXPENSE=20,
                    FINANCE_EXPENSE=30,
                    OPERATE_PROFIT=100,
                    TOTAL_PROFIT=90,
                    INCOME_TAX=10,
                    PARENT_NETPROFIT=75,
                    # A source value here must not be guessed to be consolidated net income.
                    NETPROFIT=70,
                )
            ],
            (
                "balance_sheet",
                "2026-06-30",
            ): [
                _statement_row(
                    "balance_sheet",
                    TOTAL_ASSETS="1000",
                    TOTAL_LIABILITIES="420",
                    TOTAL_EQUITY="580",
                    MONETARYFUNDS="100",
                    ACCOUNTS_RECE="30",
                    DEBT_ASSET_RATIO="42.0",
                )
            ],
            (
                "cash_flow",
                "2026-06-30",
            ): [
                _statement_row(
                    "cash_flow",
                    NETCASH_OPERATE="NaN",
                    NETCASH_INVEST="-inf",
                    NETCASH_FINANCE="120",
                    CONSTRUCT_LONG_ASSET="bad-number",
                    CCE_ADD="5",
                )
            ],
        }
    )
    provider = _provider(client)

    income = provider.fetch_period("income", "2026-06-30").row(0, named=True)
    assert income["revenue"] == 1234.5
    assert income["operating_cost"] == 500.0
    assert income["selling_expense"] == 10.0
    assert income["admin_expense"] == 20.0
    assert income["financial_expense"] == 30.0
    assert income["operating_profit"] == 100.0
    assert income["total_profit"] == 90.0
    assert income["income_tax"] == 10.0
    assert income["net_income_attributable"] == 75.0
    assert income["net_income"] is None
    assert provider.fetch_period("income", "2026-06-30").schema["net_income"] == pl.Float64

    balance = provider.fetch_period("balance_sheet", "2026-06-30").row(0, named=True)
    assert balance["total_assets"] == 1000.0
    assert balance["total_liabilities"] == 420.0
    assert balance["total_equity"] == 580.0
    assert balance["cash_and_equivalents"] == 100.0
    assert balance["accounts_receivable"] == 30.0
    assert balance["debt_to_asset_ratio"] == 42.0

    cash = provider.fetch_period("cash_flow", "2026-06-30")
    row = cash.row(0, named=True)
    assert row["net_operating_cash_flow"] is None
    assert row["net_investing_cash_flow"] is None
    assert row["net_financing_cash_flow"] == 120.0
    assert row["capex"] is None
    assert row["net_cash_change"] == 5.0


def test_metrics_filters_requested_a_shares_and_uses_latest_safe_availability_date(as_of):
    rows = [
        _metrics_row(
            symbol="600519.SH",
            notice="2026-08-15",
            update="2026-08-20",
            BASIC_EPS="4.2",
            BPS="95.5",
            WEIGHTAVG_ROE="16.75",
            XSMLL="89.2",
            YSTZ="4.1",
            SJLTZ="5.2",
            MGJYXJJE="21.5",
        ),
        # Restatement with a later verifiable update date replaces the earlier row.
        _metrics_row(
            symbol="600519.SH",
            notice="2026-08-15",
            update="2026-08-25",
            BASIC_EPS="4.3",
            BPS="96.5",
            WEIGHTAVG_ROE="17.75",
            XSMLL="90.2",
            YSTZ="4.2",
            SJLTZ="5.3",
            MGJYXJJE="22.5",
        ),
        # Exact duplicate is collapsed.
        _metrics_row(
            symbol="600519.SH",
            notice="2026-08-15",
            update="2026-08-25",
            BASIC_EPS="4.3",
            BPS="96.5",
            WEIGHTAVG_ROE="17.75",
            XSMLL="90.2",
            YSTZ="4.2",
            SJLTZ="5.3",
            MGJYXJJE="22.5",
        ),
        # Report-date-only data is usable when UPDATE_DATE is the available date.
        _metrics_row(
            symbol="000005.SZ",
            notice=None,
            update="2026-08-13",
            BPS="3.4",
            WEIGHTAVG_ROE="7.5",
        ),
        # Unrequested symbols are filtered after the bulk response.
        _metrics_row(symbol="000001.SZ", BPS="9"),
        _metrics_row(symbol="000002.SZ", security_type="058001099", BPS="8"),
        _metrics_row(symbol="000003.SZ", isnew="0", BPS="7"),
        _metrics_row(symbol="000004.SZ", notice=None, update=None, BPS="6"),
        # A future update cannot be used for today's historical view.
        _metrics_row(
            symbol="600519.SH",
            notice="2026-08-15",
            update="2026-10-03",
            BPS="999",
        ),
        # Wrong and future report periods are discarded.
        _metrics_row(symbol="600519.SH", period="2026-03-31", BPS="888"),
        _metrics_row(symbol="600519.SH", period="2026-12-31", BPS="777"),
    ]
    provider = _provider(_FakeFinancialClient(reports={("metrics", "2026-06-30"): rows}))

    frame = provider.fetch_period("metrics", "2026-06-30", ["600519.SH", "000005.SZ"])
    assert frame.height == 2
    main = frame.filter(pl.col("symbol") == "600519.SH").row(0, named=True)
    assert main["period_end"] == "2026-06-30"
    assert main["announce_date"] == "2026-08-25"
    assert main["eps_basic"] == 4.3
    assert main["bps"] == 96.5
    assert main["roe"] == 17.75  # Percent values are preserved as percentages.
    assert main["gross_margin"] == 90.2
    assert main["revenue_yoy"] == 4.2
    assert main["net_income_yoy"] == 5.3
    assert main["MGJYXJJE"] == 22.5
    assert main["net_margin"] is None
    assert main["debt_to_asset_ratio"] is None
    assert frame.schema["net_margin"] == pl.Float64
    assert frame.schema["debt_to_asset_ratio"] == pl.Float64
    update_only = frame.filter(pl.col("symbol") == "000005.SZ").row(0, named=True)
    assert update_only["announce_date"] == "2026-08-13"
    assert update_only["bps"] == 3.4


def test_missing_announcement_date_future_period_and_future_announcement_are_omitted(as_of):
    fake = _FakeFinancialClient(
        reports={
            (
                "income",
                "2026-06-30",
            ): [
                _statement_row("income", symbol="600519.SH", notice=None, TOTAL_PROFIT=10),
                _statement_row("income", symbol="000001.SZ", notice="2026-10-03", TOTAL_PROFIT=20),
            ]
        }
    )
    provider = _provider(fake)
    assert provider.fetch_period("income", "2026-06-30").is_empty()
    assert provider.fetch_period("income", "2026-12-31").is_empty()
    assert fake.calls == [("income", "2026-06-30")]


@pytest.mark.parametrize(
    ("table", "source_field"),
    [
        ("income", "TOTAL_PROFIT"),
        ("balance_sheet", "TOTAL_ASSETS"),
        ("cash_flow", "NETCASH_OPERATE"),
    ],
)
def test_statement_update_date_is_the_availability_date_and_future_update_is_rejected(
    as_of, table, source_field
):
    rows = [
        _statement_row(
            table,
            symbol="600519.SH",
            notice="2026-08-15",
            UPDATE_DATE="2026-08-25",
            **{source_field: 10},
        ),
        _statement_row(
            table,
            symbol="000001.SZ",
            notice="2026-08-15",
            UPDATE_DATE="2026-10-03",
            **{source_field: 20},
        ),
    ]
    provider = _provider(_FakeFinancialClient(reports={(table, "2026-06-30"): rows}))

    frame = provider.fetch_period(table, "2026-06-30")

    assert frame.select("symbol", "announce_date").to_dicts() == [
        {"symbol": "600519.SH", "announce_date": "2026-08-25"}
    ]


def test_metrics_get_financials_fills_null_debt_ratio_from_same_period_balance(as_of):
    client = _FakeFinancialClient(
        reports={
            (
                "metrics",
                "2026-06-30",
            ): [
                _metrics_row(
                    symbol="600519.SH",
                    notice="2026-08-15",
                    update="2026-08-20",
                    BPS=95.5,
                ),
                _metrics_row(
                    symbol="000001.SZ",
                    notice="2026-08-16",
                    update="2026-08-21",
                    BPS=18.5,
                ),
                _metrics_row(
                    symbol="000002.SZ",
                    notice="2026-08-17",
                    update="2026-08-22",
                    BPS=9.5,
                ),
            ],
            (
                "balance_sheet",
                "2026-06-30",
            ): [
                _statement_row(
                    "balance_sheet",
                    symbol="600519.SH",
                    notice="2026-08-15",
                    UPDATE_DATE="2026-09-01",
                    DEBT_ASSET_RATIO="42.5",
                ),
                _statement_row(
                    "balance_sheet",
                    symbol="000001.SZ",
                    notice="2026-08-25",
                    UPDATE_DATE="2026-09-03",
                    DEBT_ASSET_RATIO="53.5",
                ),
            ],
        }
    )
    provider = _provider(client)

    frame = provider.get_financials(
        "metrics", ["600519.SH", "000001.SZ", "000002.SZ"], latest_only=True
    )
    rows = {row["symbol"]: row for row in frame.to_dicts()}

    assert rows["600519.SH"]["debt_to_asset_ratio"] == 42.5
    assert rows["600519.SH"]["announce_date"] == "2026-09-01"
    assert rows["000001.SZ"]["debt_to_asset_ratio"] == 53.5
    assert rows["000001.SZ"]["announce_date"] == "2026-09-03"
    assert rows["000002.SZ"]["debt_to_asset_ratio"] is None
    assert rows["000002.SZ"]["announce_date"] == "2026-08-22"
    assert client.calls == [
        ("metrics", "2026-09-30"),
        ("metrics", "2026-06-30"),
        ("balance_sheet", "2026-06-30"),
    ]


def test_metrics_balance_merge_only_fills_nulls_and_advances_filled_row_date():
    metrics = pl.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ", "000002.SZ"],
            "period_end": ["2026-06-30"] * 3,
            "announce_date": ["2026-08-20", "2026-08-21", "2026-08-22"],
            "debt_to_asset_ratio": [60.0, None, None],
            "bps": [95.0, 18.0, 9.0],
        },
        schema={
            "symbol": pl.String,
            "period_end": pl.String,
            "announce_date": pl.String,
            "debt_to_asset_ratio": pl.Float64,
            "bps": pl.Float64,
        },
    )
    balance = pl.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ"],
            "period_end": ["2026-06-30"] * 2,
            "announce_date": ["2026-09-01", "2026-09-03"],
            "debt_to_asset_ratio": [42.5, 53.5],
        },
        schema={
            "symbol": pl.String,
            "period_end": pl.String,
            "announce_date": pl.String,
            "debt_to_asset_ratio": pl.Float64,
        },
    )

    merged = merge_metrics_with_balance(metrics, balance)
    rows = {row["symbol"]: row for row in merged.to_dicts()}

    assert rows["600519.SH"]["debt_to_asset_ratio"] == 60.0
    assert rows["600519.SH"]["announce_date"] == "2026-08-20"
    assert rows["000001.SZ"]["debt_to_asset_ratio"] == 53.5
    assert rows["000001.SZ"]["announce_date"] == "2026-09-03"
    assert rows["000002.SZ"]["debt_to_asset_ratio"] is None
    assert rows["000002.SZ"]["announce_date"] == "2026-08-22"
    assert rows["000001.SZ"]["bps"] == 18.0


def test_metrics_balance_source_failure_fails_closed(as_of):
    client = _FakeFinancialClient(
        reports={
            ("metrics", "2026-06-30"): [
                _metrics_row(symbol="600519.SH", BPS=95.5)
            ]
        },
        errors={
            ("balance_sheet", "2026-06-30"): EastmoneyError("balance source failed")
        },
    )

    with pytest.raises(EastmoneyError, match="balance source failed"):
        _provider(client).get_financials("metrics", ["600519.SH"], latest_only=True)


def test_historical_metrics_accept_isnew_zero_with_valid_report_and_update_dates(as_of):
    fake = _FakeFinancialClient(
        reports={
            (
                "metrics",
                "2024-09-30",
            ): [
                _metrics_row(
                    period="2024-09-30",
                    symbol="000001.SZ",
                    notice="2024-10-19",
                    update="2025-10-25",
                    isnew="0",
                    SECURITY_TYPE_CODE="058001008",
                    BPS="18.25",
                    WEIGHTAVG_ROE="9.4",
                )
            ]
        }
    )

    frame = _provider(fake).fetch_period("metrics", "2024-09-30", ["000001.SZ"])

    assert frame.height == 1
    row = frame.row(0, named=True)
    assert row["symbol"] == "000001.SZ"
    assert row["period_end"] == "2024-09-30"
    assert row["announce_date"] == "2025-10-25"
    assert row["bps"] == 18.25
    assert row["roe"] == 9.4


def test_conflicting_same_date_duplicate_rows_fail_closed(as_of):
    rows = [
        _metrics_row(symbol="600519.SH", update="2026-08-20", BPS="95"),
        _metrics_row(symbol="600519.SH", update="2026-08-20", BPS="96"),
    ]
    provider = _provider(_FakeFinancialClient(reports={("metrics", "2026-06-30"): rows}))
    with pytest.raises(EastmoneyError, match="conflicting"):
        provider.fetch_period("metrics", "2026-06-30")


def test_latest_only_falls_back_per_symbol_and_all_history_uses_eight_completed_quarters(as_of):
    latest_row = _statement_row("income", period="2026-06-30", TOTAL_PROFIT=60)
    older_rows = [
        _statement_row("income", period="2026-03-31", TOTAL_PROFIT=30),
        _statement_row(
            "income", period="2026-03-31", symbol="000001.SZ", TOTAL_PROFIT=31
        ),
    ]
    client = _FakeFinancialClient(
        reports={
            ("income", "2026-06-30"): [latest_row],
            ("income", "2026-03-31"): older_rows,
        }
    )
    provider = _provider(client)
    latest = provider.get_financials(
        "income", ["600519.SH", "000001.SZ"], latest_only=True
    )
    assert latest.select("symbol", "period_end").to_dicts() == [
        {"symbol": "000001.SZ", "period_end": "2026-03-31"},
        {"symbol": "600519.SH", "period_end": "2026-06-30"},
    ]
    assert [period for _, period in client.calls] == [
        "2026-09-30",
        "2026-06-30",
        "2026-03-31",
    ]

    all_history_client = _FakeFinancialClient(
        reports={("income", "2026-06-30"): [latest_row]}
    )
    all_history = _provider(all_history_client).get_financials(
        "income", ["600519.SH"], latest_only=False
    )
    assert all_history.height == 1
    assert [period for _, period in all_history_client.calls] == [
        "2026-09-30",
        "2026-06-30",
        "2026-03-31",
        "2025-12-31",
        "2025-09-30",
        "2025-06-30",
        "2025-03-31",
        "2024-12-31",
    ]


def test_future_fetch_period_does_not_call_source_and_shares_are_empty(as_of):
    client = _FakeFinancialClient()
    provider = _provider(client)
    assert provider.fetch_period("income", "2026-12-31").is_empty()
    assert provider.get_financials("shares", ["600519.SH"]).is_empty()
    assert client.calls == []
