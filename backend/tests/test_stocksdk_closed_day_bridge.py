"""Exercise the actual JS bridge with an offline SDK, including fallback calls."""

import copy
import datetime as dt
import json
import shutil
import subprocess

import pytest

from app.plugins.stocksdk import bridge


def _quote(symbol="600519.SH"):
    code, market = symbol.split(".")
    return {
        "code": code, "marketId": {"SH": "1", "SZ": "51"}[market],
        "source": "tencent", "time": "20261029161500",
        "timestamp": int(dt.datetime(2026, 10, 29, 8, 15, tzinfo=dt.UTC).timestamp() * 1000),
        "open": 10, "high": 11, "low": 9, "price": 10.5,
        "volume": 10000, "amount": 1050,
    }


def _run(tmp_path, quotes, *, job_overrides=None, fail=False, now_hour=9, daily_rows=None):
    sdk = tmp_path / "node_modules" / "stock-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(
        json.dumps({"type": "module", "main": "index.js"}), encoding="utf-8",
    )
    now = int(dt.datetime(2026, 10, 29, now_hour, tzinfo=dt.UTC).timestamp() * 1000)
    (sdk / "index.js").write_text(
        f"""Date.now = () => {now};
export class StockSDK {{
  constructor(config) {{
    this.quotes = {{cn: async codes => {{
      if ({str(fail).lower()}) throw new Error('offline');
      return {json.dumps(quotes)};
    }}}};
    this.kline = {{cn: async (sym, opts) => {json.dumps(daily_rows)} ?? [{{fallback: true, sym, opts, config}}]}};
  }}
}}
""", encoding="utf-8",
    )
    script = tmp_path / "bridge.mjs"
    shutil.copyfile(bridge._BRIDGE_MJS, script)
    job = {
        "op": "daily", "symbols": ["600519.SH"], "asset_type": "stock",
        "start": "20261029", "end": "20261029", "closed_day": "20261029",
    }
    job.update(job_overrides or {})
    proc = subprocess.run(
        ["node", str(script)], input=json.dumps(job), capture_output=True,
        text=True, encoding="utf-8", timeout=20, check=True,
    )
    result = json.loads(proc.stdout)
    assert result["ok"], result
    return result["rows"]


def test_valid_closed_snapshot_uses_native_units_and_timestamp(tmp_path):
    q = _quote()
    row = _run(tmp_path, [q])["600519.SH"][0]
    assert row == {
        "date": "2026-10-29", "open": 10, "high": 11, "low": 9,
        "close": 10.5, "volume": 10000, "amount": 10500000,
        "timestamp": q["timestamp"],
    }


def test_kcb_volume_is_converted_once_and_only_missing_symbols_fallback(tmp_path):
    q = _quote("688981.SH")
    q["volume"] *= 100
    rows = _run(tmp_path, [q], job_overrides={"symbols": ["600519.SH", "688981.SH"]})
    assert rows["600519.SH"][0]["fallback"]
    assert rows["688981.SH"][0]["volume"] == 10000
    assert rows["688981.SH"][0]["amount"] == 10500000


@pytest.mark.parametrize("patch", [
    {"time": "20261029"}, {"time": 20261029161500},
    {"time": "20261029145959"}, {"time": "20261029246000"},
    {"time": "20260230161500"}, {"time": "20261028161500"},
    {"timestamp": None}, {"timestamp": 1793261700001},
    {"time": "20261029183000", "timestamp": 1793269800000},
    {"source": "other"}, {"volume": 0}, {"amount": 0},
    {"low": 12}, {"high": 8}, {"price": -1},
    {"amount": 105000}, {"marketId": "51"},
])
def test_invalid_quote_falls_back_to_original_daily(tmp_path, patch):
    q = _quote()
    q.update(patch)
    assert _run(tmp_path, [q])["600519.SH"][0]["fallback"]


@pytest.mark.parametrize("job", [
    {"closed_day": None}, {"asset_type": "etf"}, {"asset_type": "index"},
    {"adjust": "qfq"}, {"adjust": "hfq"}, {"period": "weekly"},
    {"start": "20261028"}, {"end": "20261030"},
])
def test_historical_adjusted_and_other_asset_requests_keep_original_route(tmp_path, job):
    assert _run(tmp_path, [_quote()], job_overrides=job)["600519.SH"][0]["fallback"]


def test_duplicates_failed_batch_and_before_buffer_fallback(tmp_path):
    q = _quote()
    first = _run(tmp_path / "duplicates", [q, copy.deepcopy(q)])
    assert first["600519.SH"][0]["fallback"]
    failed = _run(tmp_path / "failed", [q], fail=True)
    assert failed["600519.SH"][0]["fallback"]
    assert failed["600519.SH"][0]["config"]["providerPolicies"]["tencent"] == {
        "timeout": 5000, "retry": {"maxRetries": 0},
    }
    early = _run(tmp_path / "early", [q], now_hour=7)
    assert early["600519.SH"][0]["fallback"]


def test_ambiguous_index_etf_and_bj_codes_are_not_snapshotted(tmp_path):
    symbols = ["000001.SH", "510300.SH", "159919.SZ", "920157.BJ"]
    rows = _run(tmp_path, [_quote()], job_overrides={"symbols": symbols})
    assert all(rows[sym][0]["fallback"] for sym in symbols)


def test_closed_history_date_marker_is_not_a_live_quote_clock(tmp_path):
    midnight = int(dt.datetime(2026, 10, 28, 16, tzinfo=dt.UTC).timestamp() * 1000)
    bar = {"date": "2026-10-29", "timestamp": midnight, "close": 10.5}
    row = _run(tmp_path / "closed", [], daily_rows=[bar])["600519.SH"][0]
    assert row == {**bar, "timestamp": None}
    forming = _run(tmp_path / "forming", [], daily_rows=[bar], now_hour=6)["600519.SH"][0]
    assert forming["timestamp"] == midnight
    actual_clock = {**bar, "timestamp": _quote()["timestamp"]}
    actual = _run(tmp_path / "actual", [], daily_rows=[actual_clock])["600519.SH"][0]
    assert actual["timestamp"] == actual_clock["timestamp"]


def test_past_history_clears_only_date_marker_not_weekly_bar_clock(tmp_path):
    stamp = int(dt.datetime(2026, 10, 27, 16, tzinfo=dt.UTC).timestamp() * 1000)
    bar = {"date": "2026-10-28", "timestamp": stamp, "close": 10.5}
    past = _run(tmp_path / "past", [], daily_rows=[bar], now_hour=6)["600519.SH"][0]
    assert past["timestamp"] is None
    weekly = _run(tmp_path / "weekly", [], daily_rows=[bar], job_overrides={"period": "weekly"})["600519.SH"][0]
    assert weekly["timestamp"] == stamp
