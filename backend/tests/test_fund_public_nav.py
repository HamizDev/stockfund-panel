"""Public Eastmoney NAV fallback parsing; fixtures only, no external requests."""

from __future__ import annotations

import json
import threading
from datetime import UTC, date, datetime

import pytest
from app.custom.fund import public_nav


def test_rank_request_uses_beijing_calendar_on_utc_host(monkeypatch):
    class MarketClock:
        @staticmethod
        def now(tz):
            assert tz.utcoffset(None).total_seconds() == 8 * 3600
            return datetime(2026, 9, 30, 16, 30, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(public_nav, "datetime", MarketClock)
    assert public_nav._rank_params("混合型")["ed"] == "2026-10-01"


@pytest.fixture(autouse=True)
def _clear_rank_cache(monkeypatch):
    with public_nav._RANK_CACHE_LOCK:
        public_nav._RANK_CACHE.clear()
    yield
    with public_nav._RANK_CACHE_LOCK:
        public_nav._RANK_CACHE.clear()


def _rank_row(
    code="000001",
    name="华夏成长混合",
    nav_date="2026-09-29",
    nav="1.246",
    fee="0.15%",
    growth_1w="1.20%",
    growth_1m="-0.50%",
    growth_3m="2.00",
    growth_6m="3.00",
    growth_1y="4.00",
    growth_2y="5.00",
    growth_3y="6.00",
):
    fields = [""] * 21
    fields[0] = code
    fields[1] = name
    fields[3] = nav_date
    fields[4] = nav
    fields[7:14] = [growth_1w, growth_1m, growth_3m, growth_6m, growth_1y, growth_2y, growth_3y]
    fields[20] = fee
    return ",".join(fields)


def _rank_response(*rows):
    # The production response wraps datas in a JavaScript object; only its JSON
    # string array is relevant to the parser.
    return "var rankData = {\"datas\": " + json.dumps(list(rows), ensure_ascii=False) + ", \"total\": 1};"


def _millis(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)


def test_fetch_rank_maps_source_indexes_and_preserves_leading_zero(monkeypatch):
    calls = []
    body = _rank_response(_rank_row())

    class Response:
        text = body

    def fake_get(url, params=None):
        calls.append((url, params))
        return Response()

    monkeypatch.setattr(public_nav, "_get", fake_get)
    rows = public_nav.fetch_rank("混合型")

    assert len(calls) == 1
    assert calls[0][0] == "https://fund.eastmoney.com/data/rankhandler.aspx"
    assert calls[0][1]["ft"] == "hh"
    assert calls[0][1]["sc"] == "1nzf"
    assert calls[0][1]["pn"] == "30000"
    assert rows == [{
        "code": "000001",
        "name": "华夏成长混合",
        "nav_date": "2026-09-29",
        "nav": 1.246,
        "purchase_fee_text": "0.15%",
        "growth_1w": 1.2,
        "growth_1m": -0.5,
        "growth_3m": 2.0,
        "growth_6m": 3.0,
        "growth_1y": 4.0,
        "growth_2y": 5.0,
        "growth_3y": 6.0,
    }]


def test_rank_rejects_missing_shapes_and_nonfinite_values():
    assert public_nav._parse_rank("var rankData = {rows: []};") == []
    assert public_nav._parse_rank('var rankData = {datas: ["ok", 3]};') == []
    assert public_nav._parse_rank('var rankData = {datas: ["unterminated]}') == []

    bad_values = _rank_row(nav="Infinity", growth_1w="NaN", growth_1m="-Infinity")
    row = public_nav._parse_rank(_rank_response(bad_values))[0]
    assert row["nav"] is None
    assert row["growth_1w"] is None
    assert row["growth_1m"] is None
    assert row["purchase_fee_text"] == "0.15%"


def test_rank_params_map_type_and_clamp_february_29_start_date():
    params = public_nav._rank_params("股票型", date(2024, 2, 29))
    assert params == {
        "op": "ph",
        "dt": "kf",
        "ft": "gp",
        "rs": "",
        "gs": "0",
        "sc": "1nzf",
        "st": "desc",
        "sd": "2023-02-28",
        "ed": "2024-02-29",
        "qdii": "",
        "tabSubtype": ",,,,,",
        "pi": "1",
        "pn": "30000",
        "dx": "1",
    }
    with pytest.raises(ValueError):
        public_nav._rank_params("未知类型", date(2024, 2, 29))


def test_rank_cache_is_per_type_six_hours_and_returns_copies(monkeypatch):
    clock = [100.0]
    calls = []
    responses = iter([
        _rank_response(_rank_row()),
        _rank_response(_rank_row(code="000002", name="第二只基金")),
        _rank_response(_rank_row(code="000003", name="第三只基金")),
    ])

    class Response:
        def __init__(self, text):
            self.text = text

    def fake_get(_url, params=None):
        calls.append(params["ft"])
        return Response(next(responses))

    monkeypatch.setattr(public_nav, "_rank_cache_now", lambda: clock[0])
    monkeypatch.setattr(public_nav, "_get", fake_get)

    first = public_nav.fetch_rank("股票型")
    first[0]["name"] = "caller mutation"
    clock[0] += public_nav._RANK_CACHE_TTL_SECONDS - 1
    cached = public_nav.fetch_rank("股票型")
    mixed = public_nav.fetch_rank("混合型")
    assert cached[0]["name"] == "华夏成长混合"
    assert mixed[0]["code"] == "000002"
    assert calls == ["gp", "hh"]

    clock[0] = 100.0 + public_nav._RANK_CACHE_TTL_SECONDS
    expired = public_nav.fetch_rank("股票型")
    assert expired[0]["code"] == "000003"
    assert calls == ["gp", "hh", "gp"]


def test_rank_empty_and_failed_fetches_are_not_cached(monkeypatch):
    calls = []

    class Response:
        text = _rank_response()

    def fake_get(_url, params=None):
        calls.append(params["ft"])
        if len(calls) <= 2:
            return Response()
        raise TimeoutError("fixture timeout")

    monkeypatch.setattr(public_nav, "_get", fake_get)
    assert public_nav.fetch_rank("混合型") == []
    assert public_nav.fetch_rank("混合型") == []
    with pytest.raises(TimeoutError):
        public_nav.fetch_rank("混合型")
    with pytest.raises(TimeoutError):
        public_nav.fetch_rank("混合型")
    assert calls == ["hh", "hh", "hh", "hh"]


def test_rank_concurrent_cache_miss_is_coalesced(monkeypatch):
    network_started = threading.Event()
    second_lock_attempt = threading.Event()
    release_network = threading.Event()
    calls = []

    class TrackingLock:
        def __init__(self):
            self._lock = threading.Lock()

        def __enter__(self):
            if threading.current_thread().name == "rank-second":
                second_lock_attempt.set()
            self._lock.acquire()
            return self

        def __exit__(self, *_exc):
            self._lock.release()

    class Response:
        text = _rank_response(_rank_row())

    def fake_get(_url, params=None):
        calls.append(params["ft"])
        network_started.set()
        if not release_network.wait(timeout=3):
            raise TimeoutError("fixture release timeout")
        return Response()

    monkeypatch.setattr(public_nav, "_RANK_CACHE_LOCK", TrackingLock())
    monkeypatch.setattr(public_nav, "_get", fake_get)
    results = []
    errors = []

    def worker():
        try:
            results.append(public_nav.fetch_rank("债券型"))
        except Exception as exc:  # surfaced to the assertions in the main thread
            errors.append(exc)

    first = threading.Thread(target=worker, name="rank-first")
    second = threading.Thread(target=worker, name="rank-second")
    first.start()
    assert network_started.wait(timeout=1)
    second.start()
    assert second_lock_attempt.wait(timeout=1)
    release_network.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert len(results) == 2
    assert results[0] == results[1]
    assert calls == ["zq"]


def test_nav_decodes_only_unit_series_and_converts_epoch_to_shanghai_date():
    rows = [
        {"x": _millis("2024-02-29T16:30:00"), "y": 1.25, "equityReturn": 0.16},
        {"x": _millis("2024-02-28T16:30:00"), "y": 1.2, "equityReturn": -0.2},
        {"x": _millis("2024-03-01T16:30:00"), "y": 1.3, "equityReturn": float("nan")},
        {"x": _millis("2024-03-02T16:30:00"), "y": float("inf"), "equityReturn": 1.0},
    ]
    body = (
        "var Data_ACWorthTrend = [{\"x\": 1, \"y\": 99}];\n"
        "var Data_netWorthTrend = " + json.dumps(rows) + ";\nwindow.after = true;"
    )

    result = public_nav._parse_nav(body)

    assert result == [
        {"date": "2024-02-29", "unit_nav": 1.2, "growth": -0.2},
        {"date": "2024-03-01", "unit_nav": 1.25, "growth": 0.16},
        {"date": "2024-03-02", "unit_nav": 1.3, "growth": None},
    ]
    assert all(set(row) == {"date", "unit_nav", "growth"} for row in result)


def test_fetch_nav_validates_code_and_uses_only_public_nav_url(monkeypatch):
    calls = []

    class Response:
        text = 'var Data_netWorthTrend = [{"x": 1790640000000, "y": 1.2, "equityReturn": 0.1}];'

    def fake_get(url, params=None):
        calls.append((url, params))
        return Response()

    monkeypatch.setattr(public_nav, "_get", fake_get)
    result = public_nav.fetch_nav("000001")
    assert len(calls) == 1
    assert calls[0] == ("https://fund.eastmoney.com/pingzhongdata/000001.js", None)
    assert result[0]["unit_nav"] == 1.2
    with pytest.raises(ValueError):
        public_nav.fetch_nav("000001.SH")
