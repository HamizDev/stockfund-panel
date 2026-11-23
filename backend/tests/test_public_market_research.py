"""Public Tencent research transport and ETF daily adapter contracts."""
import json
from datetime import UTC, date, datetime

import httpx
import pytest

from app.plugins.txquote import research
from app.plugins.txquote.provider import TxQuoteProvider
from app.services.public_market_http import PublicDataError, PublicMarketHTTP


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def transport(handler):
    clock = Clock()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PublicMarketHTTP(clock=clock, sleep=clock.sleep, client=client), clock


def test_shared_tencent_budget_cache_and_refresh():
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="ok")

    client, clock = transport(handle)
    for url in ("https://ifzq.gtimg.cn/a", "https://stock.gtimg.cn/b"):
        assert client.text(url) == "ok"
    assert clock.now == 1 and len(calls) == 2
    client.text("https://stock.gtimg.cn/b")
    assert len(calls) == 2
    client.text("https://stock.gtimg.cn/b", ttl=0)
    assert len(calls) == 3 and clock.now == 2


@pytest.mark.parametrize(("status", "cooldown"), [(403, 300), (429, 120), (503, 2)])
def test_rate_limit_stops_shared_source_requests(status, cooldown):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers={"retry-after": "120"})

    client, _ = transport(handle)
    with pytest.raises(PublicDataError):
        client.text("https://ifzq.gtimg.cn/a")
    with pytest.raises(PublicDataError, match="cooling"):
        client.text("https://stock.gtimg.cn/b")
    assert len(calls) == 1
    assert client.status()["tencent"]["cooldown_seconds"] == cooldown


def test_transport_restricts_hosts_and_response_size():
    client, _ = transport(lambda _: httpx.Response(200, content=b"x" * 2_000_001))
    with pytest.raises(ValueError, match="host"):
        client.text("https://example.com/")
    with pytest.raises(PublicDataError, match="budget"):
        client.text("https://ifzq.gtimg.cn/a")


def daily_payload(symbol="sh510300", rows=None):
    return {"data": {symbol: {"day": rows or [["2026-11-06", ".4", ".41", ".42", ".39", "100"]]}}}


def test_raw_daily_units_unknown_amount_and_no_qfq_substitution():
    row = research.parse_daily(
        daily_payload(), "510300.SH", date(2026, 11, 6), date(2026, 11, 6)
    )[0]
    assert row["close"] == .41 and row["volume"] == 100 and row["amount"] is None
    star = research.parse_daily(
        daily_payload("sh688001"), "688001.SH", date(2026, 11, 6), date(2026, 11, 6)
    )[0]
    assert star["volume"] == 1
    with pytest.raises(ValueError, match="日期"):
        research.parse_daily(daily_payload(), "510300.SH", date(2026, 10, 1), date(2026, 10, 2))
    with pytest.raises(ValueError):
        research.parse_daily(
            {"data": {"sh510300": {"qfqday": []}}},
            "510300.SH", date.min, date.max,
        )


def test_raw_daily_is_bounded_and_requests_raw_field(monkeypatch):
    calls = []

    def text(_url, params, **_):
        calls.append(params["param"])
        assert params["param"].endswith(",640,")
        return json.dumps({"data": {"sh510300": {"day": []}}})

    monkeypatch.setattr(research.PUBLIC_HTTP, "text", text)
    assert research.daily(
        ["510300.SH"], datetime(2024, 1, 1), datetime(2026, 11, 6), "etf"
    ).is_empty()
    assert len(calls) == 2
    with pytest.raises(ValueError):
        research.daily(["510300.SH"] * 21, None, None, "etf")


def test_daily_normalizes_aware_window_to_china_date(monkeypatch):
    calls = []

    def text(_url, params, **_):
        calls.append(params["param"])
        return json.dumps({"data": {"sh510300": {"day": []}}})

    monkeypatch.setattr(research.PUBLIC_HTTP, "text", text)
    instant = datetime(2026, 11, 5, 20, tzinfo=UTC)
    research.daily(["510300.SH"], instant, instant, "etf")
    assert calls == ["sh510300,day,2026-11-06,2026-11-06,640,"]


def test_prints_are_partial_explicitly_not_level2_and_bounded(monkeypatch):
    monkeypatch.setattr(research, "snapshot", lambda *_: (datetime(2026, 11, 6, 15), 400.0))

    def text(_url, params=None, **_):
        return 'v_detail_data_sh510300=[0,"0/09:25:00/0.4/0/10/400/M"];' if params else ""

    monkeypatch.setattr(research.PUBLIC_HTTP, "text", text)
    result = research.trade_prints("510300.SH", max_pages=1)
    assert result["rows"][0]["volume"] == 10 and result["rows"][0]["amount"] == 400
    assert result["is_level2"] is False and result["complete"] is False and result["truncated"]
    assert result["level"] == "sampled_trade_prints"
    with pytest.raises(ValueError):
        research.trade_prints("510300.SH", max_pages=4)


def test_txquote_provider_exposes_only_explicit_bounded_research_methods(monkeypatch):
    provider = TxQuoteProvider()
    assert provider.config.datasets["daily"] is None
    assert provider.daily_price_basis == "raw"
    monkeypatch.setattr(research, "daily", lambda *args: ("daily", args))
    monkeypatch.setattr(research, "trade_prints", lambda *args, **kwargs: (args, kwargs))
    assert provider.get_daily(["510300.SH"], None, None, "etf")[0] == "daily"
    assert provider.get_trade_prints("510300.SH", max_pages=1) == (
        ("510300.SH",), {"max_pages": 1}
    )
