"""Fuyao batch daily bars: raw-price contract, bounded fallback, and failures."""

from __future__ import annotations

from datetime import date, datetime

import httpx
import polars as pl
import pytest

from app.plugins.fuyao import client as fc
from app.plugins.fuyao import provider as fp
from app.plugins.fuyao.provider import FuyaoProvider

DAY = date(2026, 11, 6)
START = fp._ms_of_date(DAY)
HISTORICAL_BATCH_PATH = "/api/a-share/prices/historical-batch"
HISTORICAL_SINGLE_PATH = "/api/a-share/prices/historical"


def _bar(day: date = DAY, close: float = 11.63) -> dict:
    return {
        "date_ms": fp._ms_of_date(day),
        "open_price": close - 0.1,
        "high_price": close + 0.2,
        "low_price": close - 0.2,
        "close_price": close,
        "volume": 1_612_611,
        "turnover": 18_754_665.93,
    }


def _payload(groups: list[dict], **changes) -> dict:
    return {
        "code": 0,
        "data": {"interval": "1d", "adjust": "none", "item": groups, **changes},
    }


def _group(symbol: str, bars: list[dict] | None = None) -> dict:
    return {"thscode": symbol, "item": [_bar()] if bars is None else bars}


def _http_client(monkeypatch, body, status: int = 200):
    calls: list[httpx.Request] = []
    real_client = fc.httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        result = body(request) if callable(body) else body
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(status, json=result)

    monkeypatch.setattr(
        fc.httpx,
        "Client",
        lambda **kwargs: real_client(
            **kwargs, transport=httpx.MockTransport(respond)
        ),
    )
    return fc.FuyaoClient(api_key="test-key"), calls


class _BatchClient:
    def __init__(
        self,
        bars: dict[str, list[dict]] | None = None,
        *,
        missing: tuple[str, ...] = (),
        batch_error: fc.FuyaoError | None = None,
        single_errors: dict[str, fc.FuyaoError] | None = None,
    ) -> None:
        self.bars = bars or {}
        self.missing = set(missing)
        self.batch_error = batch_error
        self.single_errors = single_errors or {}
        self.batch_calls: list[tuple[list[str], int, int]] = []
        self.single_calls: list[tuple[str, int, int, str]] = []

    def historical_kline_batch(self, thscodes: list[str], start_ms: int, end_ms: int):
        self.batch_calls.append((list(thscodes), start_ms, end_ms))
        if self.batch_error is not None:
            raise self.batch_error
        return {
            symbol: [
                bar
                for bar in self.bars.get(symbol, [])
                if start_ms <= bar["date_ms"] <= end_ms
            ]
            for symbol in thscodes
            if symbol not in self.missing
        }

    def historical_kline(
        self, thscode: str, start_ms: int, end_ms: int, adjust: str = "none"
    ):
        self.single_calls.append((thscode, start_ms, end_ms, adjust))
        if thscode in self.single_errors:
            raise self.single_errors[thscode]
        return [
            bar
            for bar in self.bars.get(thscode, [])
            if start_ms <= bar["date_ms"] <= end_ms
        ]


def _provider(monkeypatch, client: _BatchClient) -> FuyaoProvider:
    provider = FuyaoProvider()
    provider._client = client  # type: ignore[assignment]
    provider._daily_dump_info = lambda: None  # type: ignore[method-assign]
    provider._ensure_daily_big_dump = lambda start_d: None  # type: ignore[method-assign]

    def no_dump(*args):
        raise fc.FuyaoError("test dump unavailable")

    provider._ensure_dump = no_dump  # type: ignore[method-assign]
    monkeypatch.setattr(fp, "_HIST_INTERVAL_S", 0.0)
    return provider


def _api_error(
    *,
    code: int | str,
    message: str,
    path: str = HISTORICAL_SINGLE_PATH,
    http_status: int | None = None,
) -> fc.FuyaoError:
    return fc.FuyaoError(
        f"扶摇接口错误 code={code}: {message}",
        code=code,
        api_message=message,
        path=path,
        http_status=http_status,
    )


def test_client_batch_keeps_group_identity_and_empty_vs_missing(monkeypatch):
    payload = _payload([_group("600519.SH", []), _group("000001.SZ")])
    client, calls = _http_client(monkeypatch, payload)
    try:
        result = client.historical_kline_batch(
            ["000001.SZ", "600519.SH", "000002.SZ"], START, START
        )
    finally:
        client.close()

    assert result == {"600519.SH": [], "000001.SZ": [_bar()]}
    assert "000002.SZ" not in result
    assert calls[0].url.path == HISTORICAL_BATCH_PATH
    assert dict(calls[0].url.params) == {
        "thscodes": "000001.SZ,600519.SH,000002.SZ",
        "interval": "1d",
        "adjust": "none",
        "start": str(START),
        "end": str(START),
    }
    assert calls[0].headers["X-api-key"] == "test-key"


def test_client_batch_limits_requests_to_ten_symbols(monkeypatch):
    client, calls = _http_client(monkeypatch, _payload([]))
    try:
        assert client.historical_kline_batch([], START, START) == {}
        client.historical_kline_batch([f"{i:06d}.SZ" for i in range(10)], START, START)
        with pytest.raises(fc.FuyaoError, match="10"):
            client.historical_kline_batch([f"{i:06d}.SZ" for i in range(11)], START, START)
    finally:
        client.close()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,payload",
    [
        (403, {"code": 2002, "message": "permission denied"}),
        (404, {}),
        (200, {"code": 4001, "message": "rate limited", "data": None}),
    ],
)
def test_client_errors_preserve_envelope_and_http_status(monkeypatch, status, payload):
    client, _ = _http_client(monkeypatch, payload, status=status)
    try:
        with pytest.raises(fc.FuyaoError) as caught:
            client.historical_kline_batch(["000001.SZ"], START, START)
    finally:
        client.close()
    if status == 200:
        assert caught.value.code == 4001
        assert caught.value.api_message == "rate limited"
        assert caught.value.path == HISTORICAL_BATCH_PATH
    else:
        assert caught.value.http_status == status
        assert caught.value.path == HISTORICAL_BATCH_PATH


def test_small_daily_query_uses_raw_batch_without_dump_or_single_calls(monkeypatch):
    bars = {
        "000001.SZ": [_bar(close=11.63)],
        "600519.SH": [_bar(close=1480.12)],
    }
    client = _BatchClient(bars)
    provider = _provider(monkeypatch, client)

    def no_dump(*args):
        raise AssertionError("small query must not load a market dump")

    provider._ensure_dump = no_dump  # type: ignore[method-assign]
    provider._daily_dump_info = no_dump  # type: ignore[method-assign]
    df = provider.get_daily(
        list(bars), datetime(2026, 11, 6), datetime(2026, 11, 6)
    )

    assert df.columns == ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    assert df.schema["date"] == pl.Date
    assert dict(zip(df["symbol"], df["close"], strict=True)) == {
        "000001.SZ": 11.63,
        "600519.SH": 1480.12,
    }
    assert df["volume"].to_list() == [16126.0, 16126.0]
    assert df["amount"].to_list() == [18_754_665.93, 18_754_665.93]
    assert df["date"].to_list() == [DAY, DAY]
    assert client.batch_calls == [(["000001.SZ", "600519.SH"], START, START)]
    assert client.single_calls == []


def test_duplicate_symbols_are_requested_and_emitted_once(monkeypatch):
    client = _BatchClient({"000001.SZ": [_bar()], "600519.SH": [_bar()]})
    provider = _provider(monkeypatch, client)
    df = provider.get_daily(
        ["600519.SH", "000001.SZ", "600519.SH"],
        datetime(2026, 11, 6),
        datetime(2026, 11, 6),
    )
    assert client.batch_calls[0][0] == ["600519.SH", "000001.SZ"]
    assert df.height == 2


def test_api_batches_never_exceed_ten_symbols(monkeypatch):
    symbols = [f"{index + 1:06d}.SZ" for index in range(11)]
    client = _BatchClient({symbol: [_bar()] for symbol in symbols})
    provider = _provider(monkeypatch, client)
    result = provider.get_daily(
        symbols, datetime(2026, 11, 6), datetime(2026, 11, 6)
    )
    assert [len(call[0]) for call in client.batch_calls] == [10, 1]
    assert result.height == len(symbols)
    assert client.single_calls == []


def test_missing_group_only_uses_single_fallback_for_that_symbol(monkeypatch):
    client = _BatchClient(
        {"000001.SZ": [_bar()], "600519.SH": [_bar(close=1480.12)]},
        missing=("600519.SH",),
    )
    result = _provider(monkeypatch, client).get_daily(
        ["000001.SZ", "600519.SH"], datetime(2026, 11, 6), datetime(2026, 11, 6)
    )
    assert result.height == 2
    assert [call[0] for call in client.single_calls] == ["600519.SH"]
    assert all(call[3] == "none" for call in client.single_calls)


def test_valid_empty_group_does_not_trigger_single_fallback(monkeypatch):
    client = _BatchClient({"000001.SZ": []})
    result = _provider(monkeypatch, client).get_daily(
        ["000001.SZ"], datetime(2026, 11, 6), datetime(2026, 11, 6)
    )
    assert result.is_empty()
    assert client.single_calls == []


def test_unavailable_batch_endpoint_is_probed_once_then_falls_back(monkeypatch):
    symbols = ["000001.SZ", "600519.SH"]
    client = _BatchClient(
        {symbol: [_bar()] for symbol in symbols},
        batch_error=_api_error(
            code=404, message="not found", path=HISTORICAL_BATCH_PATH, http_status=404
        ),
    )
    result = _provider(monkeypatch, client).get_daily(
        symbols, datetime(2001, 1, 1), datetime(2026, 11, 6)
    )
    assert result.height == 2
    assert len(client.batch_calls) == 1
    assert len(client.single_calls) == 6
    assert all(call[3] == "none" for call in client.single_calls)


@pytest.mark.parametrize(
    "error",
    [
        _api_error(code=1001, message="invalid key", path=HISTORICAL_BATCH_PATH),
        _api_error(code=4001, message="rate limited", path=HISTORICAL_BATCH_PATH),
        _api_error(code=1002, message="Unknown thscode: 000001.SZ", path=HISTORICAL_BATCH_PATH),
        _api_error(code=0, message="unauthorized", path=HISTORICAL_BATCH_PATH, http_status=401),
        _api_error(code=0, message="forbidden", path=HISTORICAL_BATCH_PATH, http_status=403),
        _api_error(code=0, message="too many requests", path=HISTORICAL_BATCH_PATH, http_status=429),
    ],
)
def test_batch_auth_rate_and_other_errors_fail_closed_without_single_fallback(monkeypatch, error):
    client = _BatchClient(batch_error=error)
    with pytest.raises(fc.FuyaoError) as caught:
        _provider(monkeypatch, client).get_daily(
            ["000001.SZ", "600519.SH"], datetime(2026, 11, 6), datetime(2026, 11, 6)
        )
    assert client.single_calls == []
    assert caught.value.code == error.code
    assert caught.value.api_message == error.api_message
    assert caught.value.http_status == error.http_status


@pytest.mark.parametrize(
    "error",
    [
        _api_error(code=1001, message="invalid key", path="/api/dump/market-dumps/daily-k-10d/download-url"),
        _api_error(code=4001, message="rate limited", path="/api/dump/market-dumps/daily-k-10d/download-url"),
        _api_error(code=0, message="unauthorized", path="/api/dump/market-dumps/daily-k-10d/download-url", http_status=401),
        _api_error(code=0, message="forbidden", path="/api/dump/market-dumps/daily-k-10d/download-url", http_status=403),
        _api_error(code=0, message="too many requests", path="/api/dump/market-dumps/daily-k-10d/download-url", http_status=429),
    ],
)
def test_dump_auth_and_rate_limit_errors_do_not_fall_back_to_history(monkeypatch, error):
    symbols = [f"{index + 1:06d}.SZ" for index in range(11)]
    client = _BatchClient({symbol: [_bar()] for symbol in symbols})
    provider = _provider(monkeypatch, client)

    def denied_dump(*args):
        raise error

    provider._ensure_dump = denied_dump  # type: ignore[method-assign]
    with pytest.raises(fc.FuyaoError) as caught:
        provider.get_daily(
            symbols, datetime(2026, 11, 6), datetime(2026, 11, 6)
        )
    assert caught.value.code == error.code
    assert caught.value.http_status == error.http_status
    assert client.batch_calls == client.single_calls == []


def test_daily_dump_info_propagates_structured_auth_failure(monkeypatch, tmp_path):
    error = _api_error(
        code=4001,
        message="rate limited",
        path="/api/dump/market-dumps/daily-k/download-url",
    )
    client = _BatchClient()
    provider = _provider(monkeypatch, client)
    monkeypatch.setattr(fp, "_cache_dir", lambda: tmp_path)

    def denied_url(*args):
        raise error

    provider._ensure_dump_path = denied_url  # type: ignore[method-assign]
    with pytest.raises(fc.FuyaoError) as caught:
        FuyaoProvider._daily_dump_info(provider)
    assert caught.value.code == 4001


def test_big_dump_tail_propagates_structured_10d_auth_failure(monkeypatch, tmp_path):
    error = _api_error(
        code=0,
        message="forbidden",
        path="/api/dump/market-dumps/daily-k-10d/download-url",
        http_status=403,
    )
    symbols = [f"{index + 1:06d}.SZ" for index in range(11)]
    client = _BatchClient({symbol: [_bar()] for symbol in symbols})
    provider = _provider(monkeypatch, client)
    provider._daily_dump_info = lambda: (
        tmp_path / "cached-daily.parquet",
        date(2026, 8, 1),
        date(2026, 8, 5),
    )

    def denied_tail(*args):
        raise error

    provider._ensure_dump = denied_tail  # type: ignore[method-assign]
    with pytest.raises(fc.FuyaoError) as caught:
        provider.get_daily(
            symbols, datetime(2026, 8, 1), datetime(2026, 8, 28)
        )
    assert caught.value.http_status == 403
    assert client.batch_calls == client.single_calls == []


def test_exact_single_unknown_symbol_is_skipped_and_other_group_is_kept(monkeypatch):
    unknown = "999999.SZ"
    client = _BatchClient(
        {"000001.SZ": [_bar()]},
        missing=(unknown,),
        single_errors={
            unknown: _api_error(
                code=1002,
                message=f"  Unknown thscode: {unknown}  ",
            )
        },
    )
    result = _provider(monkeypatch, client).get_daily(
        [unknown, "000001.SZ"], datetime(2026, 11, 6), datetime(2026, 11, 6)
    )
    assert result["symbol"].to_list() == ["000001.SZ"]
    assert [call[0] for call in client.single_calls] == [unknown]


def test_all_exact_unknown_symbols_return_empty(monkeypatch):
    symbols = ["999998.SZ", "999999.SZ"]
    client = _BatchClient(
        {},
        missing=tuple(symbols),
        single_errors={
            symbol: _api_error(code=1002, message=f"Unknown thscode: {symbol}")
            for symbol in symbols
        },
    )
    result = _provider(monkeypatch, client).get_daily(
        symbols, datetime(2026, 11, 6), datetime(2026, 11, 6)
    )
    assert result.is_empty()
    assert len(client.single_calls) == 2


def test_unknown_symbol_is_not_requested_again_in_later_ten_year_slices(monkeypatch):
    unknown = "999999.SZ"
    client = _BatchClient(
        {},
        missing=(unknown,),
        single_errors={
            unknown: _api_error(code=1002, message=f"Unknown thscode: {unknown}")
        },
    )
    result = _provider(monkeypatch, client).get_daily(
        [unknown], datetime(2001, 1, 1), datetime(2026, 11, 6)
    )
    assert result.is_empty()
    assert len(client.batch_calls) == 1
    assert len(client.single_calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        _api_error(code=1002, message="Unknown thscode"),
        _api_error(code=1002, message="Unknown thscode: 000002.SZ"),
        _api_error(code=1002, message="Unknown thscode: 000001.SZ ", path=HISTORICAL_BATCH_PATH),
        _api_error(code=4001, message="Unknown thscode: 000001.SZ"),
        _api_error(code=2002, message="Unknown thscode: 000001.SZ"),
    ],
)
def test_unknown_exception_must_match_code_message_path_and_requested_symbol(monkeypatch, error):
    symbol = "000001.SZ"
    client = _BatchClient({}, missing=(symbol,), single_errors={symbol: error})
    with pytest.raises(fc.FuyaoError) as caught:
        _provider(monkeypatch, client).get_daily(
            [symbol], datetime(2026, 11, 6), datetime(2026, 11, 6)
        )
    assert caught.value.code == error.code
    assert caught.value.api_message == error.api_message


def test_terminal_single_failure_discards_staging_and_preserves_existing_data(
    monkeypatch, tmp_path
):
    from app.data_providers import custom
    from app.services import kline_sync
    from app.tickflow.repository import DataStore, KlineRepository

    symbols = [f"{index + 1:06d}.SZ" for index in range(51)]
    terminal_symbol = symbols[-1]
    client = _BatchClient(
        {symbol: [_bar()] for symbol in symbols[:-1]},
        missing=(terminal_symbol,),
        single_errors={
            terminal_symbol: _api_error(
                code=4001, message="rate limited", path=HISTORICAL_SINGLE_PATH
            )
        },
    )
    provider = _provider(monkeypatch, client)
    monkeypatch.setattr(
        kline_sync.preferences, "get_daily_data_provider", lambda: "fuyao"
    )
    monkeypatch.setattr(custom, "provider_has_dataset", lambda *_: True)
    monkeypatch.setattr(custom, "get_provider", lambda _: provider)
    repo = KlineRepository(DataStore(tmp_path))

    target = repo.store.data_dir / "kline_daily" / f"date={DAY.isoformat()}" / "part.parquet"
    target.parent.mkdir(parents=True)
    pl.DataFrame(
        {"symbol": ["600519.SH"], "date": [DAY], "close": [1480.12]}
    ).write_parquet(target)
    before = target.read_bytes()

    with pytest.raises(fc.FuyaoError, match=terminal_symbol):
        kline_sync.sync_and_persist_daily_batch(
            symbols,
            repo,
            object(),
            start_date=datetime(2026, 11, 6),
            end_date=datetime(2026, 11, 6),
        )

    assert len(client.batch_calls) == 6
    assert target.read_bytes() == before
    assert not (repo.store.data_dir / ".daily_sync_staging").exists()
