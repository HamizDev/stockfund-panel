from datetime import date, datetime, time, timedelta, timezone

import polars as pl
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.kronos import training_coverage
from app.custom.kronos.routes import build_router

CN_TZ = timezone(timedelta(hours=8))


def session_frame(symbol: str, day: date) -> pl.DataFrame:
    stamps = [
        datetime.combine(day, start) + timedelta(minutes=offset)
        for start, count in ((time(9, 31), 120), (time(13, 1), 120))
        for offset in range(count)
    ]
    return pl.DataFrame({
        "symbol": [symbol] * len(stamps),
        "datetime": stamps,
        "open": [1.0] * len(stamps),
        "high": [1.01] * len(stamps),
        "low": [0.99] * len(stamps),
        "close": [1.0] * len(stamps),
        "volume": [1.0] * len(stamps),
    })


class MinuteRepo:
    def __init__(self, frames):
        self.frames = frames
        self.list_calls = []
        self.read_calls = []

    def list_minute_dates(self, start, end, asset_type="stock"):
        self.list_calls.append((start, end, asset_type))
        return sorted(day for day in self.frames if start <= day <= end)

    def get_minute_by_dates(self, symbols, dates, asset_type="stock"):
        self.read_calls.append((symbols, dates, asset_type))
        frames = [self.frames[day] for day in dates if day in self.frames]
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()


def test_training_coverage_counts_only_exact_complete_sessions_and_bounds_reads():
    today = date(2026, 11, 23)
    days = [date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)]
    frames = {day: session_frame("510300.SH", day) for day in days}
    auction = frames[days[0]].head(1).with_columns(
        pl.lit(datetime(2026, 11, 17, 9, 30)).alias("datetime"),
    )
    frames[days[0]] = pl.concat([frames[days[0]], auction]).sort("datetime")
    frames[days[1]] = frames[days[1]].slice(0, 239)
    duplicate = pl.concat([session_frame("510300.SH", days[2]), session_frame("510300.SH", days[2]).slice(0, 1)])
    frames[days[2]] = duplicate
    frames[today] = session_frame("510300.SH", today)
    frames[date(2026, 11, 21)] = session_frame("510300.SH", date(2026, 11, 21))
    repo = MinuteRepo(frames)

    result = training_coverage.summarize(
        repo, "510300.SH", "etf", now=datetime(2026, 11, 23, 14, 0, tzinfo=CN_TZ),
    )

    assert result == {
        "symbol": "510300.SH",
        "asset_type": "etf",
        "target_days": 20,
        "complete_days": 2,
        "from_date": "2026-09-24",
        "through_date": "2026-11-22",
        "dates": [days[0].isoformat(), days[3].isoformat()],
    }
    assert repo.list_calls == [(date(2026, 9, 24), date(2026, 11, 22), "etf")]
    assert len(repo.read_calls) == 1
    assert repo.read_calls[0][0] == ["510300.SH"]
    assert repo.read_calls[0][1] == days
    assert repo.read_calls[0][2] == "etf"


def test_training_coverage_rejects_invalid_ohlc_and_missing_volume():
    invalid_day = date(2026, 11, 19)
    missing_volume_day = date(2026, 11, 20)
    invalid_ohlc = session_frame("600519.SH", invalid_day).with_columns(
        pl.when(pl.int_range(pl.len()) == 0).then(0.0).otherwise(pl.col("low")).alias("low"),
    )
    missing_volume = session_frame("600519.SH", missing_volume_day).drop("volume")
    result = training_coverage.summarize(
        MinuteRepo({invalid_day: invalid_ohlc, missing_volume_day: missing_volume}),
        "600519.SH", "stock", now=datetime(2026, 11, 23, 16, 0, tzinfo=CN_TZ),
    )
    assert result["complete_days"] == 0
    assert result["dates"] == []


def test_training_coverage_returns_empty_for_missing_partitions():
    result = training_coverage.summarize(
        MinuteRepo({}),
        "510300.SH", "etf", now=datetime(2026, 11, 23, 16, 0, tzinfo=CN_TZ),
    )
    assert result["complete_days"] == 0
    assert result["dates"] == []


def test_training_coverage_returns_only_the_latest_twenty_valid_dates():
    start = date(2026, 10, 1)
    end = date(2026, 11, 22)
    days = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    result = training_coverage.summarize(
        MinuteRepo({day: session_frame("510300.SH", day) for day in days}),
        "510300.SH", "etf", now=datetime(2026, 11, 23, 16, 0, tzinfo=CN_TZ),
    )
    assert result["complete_days"] == 20
    assert result["dates"] == [day.isoformat() for day in days[-20:]]
    assert result["from_date"] == "2026-09-25"
    assert result["through_date"] == "2026-11-23"


def test_training_coverage_rejects_off_minute_in_session_timestamp():
    day = date(2026, 11, 20)
    frame = session_frame("510300.SH", day).with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(datetime(2026, 11, 20, 9, 31, 30))
        .otherwise(pl.col("datetime"))
        .alias("datetime"),
    )
    result = training_coverage.summarize(
        MinuteRepo({day: frame}), "510300.SH", "etf",
        now=datetime(2026, 11, 23, 16, 0, tzinfo=CN_TZ),
    )
    assert result["complete_days"] == 0


def test_training_coverage_route_validates_symbol_and_asset_type(monkeypatch):
    app = FastAPI()
    app.include_router(build_router())
    app.state.repo = object()
    seen = []

    def summarize(repo, symbol, asset_type):
        seen.append((repo, symbol, asset_type))
        return {"symbol": symbol, "asset_type": asset_type, "complete_days": 0}

    monkeypatch.setattr(training_coverage, "summarize", summarize)
    client = TestClient(app)
    path = "/api/custom/kronos/training/coverage"
    response = client.get(path, params={"symbol": "510300.SH", "asset_type": "etf"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert seen == [(app.state.repo, "510300.SH", "etf")]
    for params in (
        {"symbol": "515880.HK", "asset_type": "etf"},
        {"symbol": "not-a-symbol", "asset_type": "stock"},
        {"symbol": "600519.SH", "asset_type": "index"},
    ):
        assert client.get(path, params=params).status_code == 422
