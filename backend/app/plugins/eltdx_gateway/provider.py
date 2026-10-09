"""Raw minute bars and bounded stock collection from a research gateway."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from threading import Lock

import polars as pl

from app.market_time import cn_now

from .client import (
    INTRADAY_CHUNK_SIZE,
    EltdxGatewayClient,
    EltdxGatewayError,
    bar_datetime,
    gateway_url,
    time_window,
    wire_symbol,
)

_SCHEMA = {
    "symbol": pl.String, "datetime": pl.Datetime("us"),
    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
    "close": pl.Float64, "volume": pl.Float64, "amount": pl.Float64,
}


def availability() -> tuple[bool, str]:
    # A loader check must not start a gateway or contact market servers.
    try:
        gateway_url()
        return True, "需单独启动ELTDX 3.2.2网关; 在线状态以试拉为准"
    except EltdxGatewayError as exc:
        return False, str(exc)


@dataclass
class _Config:
    name: str = "eltdx_gateway"
    display_name: str = "通达信 ELTDX(研究分钟)"
    datasets: dict = field(default_factory=lambda: {"minute": None, "full_minute": None})
    path: None = None
    builtin: bool = True


def _number(value: object) -> float:
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
        return number
    except (ValueError, TypeError, OverflowError):
        raise EltdxGatewayError("ELTDX分钟数值缺失或无效") from None


class EltdxGatewayProvider:
    name = "eltdx_gateway"
    builtin = True
    minute_asset_types = ("stock", "etf")
    minute_max_symbols_per_request = 1
    minute_history_days = 5
    minute_price_basis = "raw"
    minute_fail_closed = True
    full_minute_min_interval_s = 300

    def __init__(self) -> None:
        self.config = _Config()
        self._client = None
        self._client_lock = Lock()
        self._intraday_lock = Lock()
        self._stats_lock = Lock()
        self._intraday_stats: dict = {}

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def get_minute(self, symbols, start_time, end_time, asset_type="stock", freq="1m", on_chunk_done=None) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame(schema=_SCHEMA)
        if len(symbols) != 1:
            raise ValueError("ELTDX研究分钟仅支持单只按需查询, 不支持全市场同步")
        if asset_type not in self.minute_asset_types or freq != "1m":
            raise ValueError("ELTDX研究源仅支持股票/ETF的1分钟K")
        symbol = symbols[0]
        wire_symbol(symbol)
        start, end = time_window(start_time, end_time)
        with self._client_lock:
            if self._client is None:
                self._client = EltdxGatewayClient()
        rows = self._client.fetch_minute(symbol, start_time, end_time)
        frame = self._bars_frame(symbol, rows, start, end)
        if on_chunk_done is not None:
            on_chunk_done(1, 1)
        return frame

    @staticmethod
    def _bars_frame(symbol, rows, start, end) -> pl.DataFrame:
        records = {}
        for row in rows:
            if not isinstance(row, dict):
                raise EltdxGatewayError("ELTDX分钟记录结构无效")
            timestamp = bar_datetime(row.get("time"))
            values = [_number(row.get(c)) for c in ("open", "high", "low", "close", "volume_lots")]
            open_, high, low, close, volume = values
            if min(open_, high, low, close) <= 0 or volume < 0 or low > min(open_, close) or high < max(open_, close) or low > high:
                raise EltdxGatewayError("ELTDX分钟价量或OHLC关系无效")
            previous = records.get(timestamp)
            if previous is not None and previous != values:
                raise EltdxGatewayError("ELTDX同一分钟出现冲突价量, 未合并发布")
            records[timestamp] = values
        result = []
        for timestamp, (open_, high, low, close, volume) in sorted(records.items()):
            if (start is not None and timestamp < start) or (end is not None and timestamp > end):
                continue
            result.append({
                "symbol": symbol, "datetime": timestamp,
                "open": open_, "high": high, "low": low, "close": close,
                # volume_lots is explicitly documented in lots. The amount unit
                # is not documented, so do not publish it as CNY or invent VWAP.
                "volume": volume, "amount": None,
            })
        return pl.DataFrame(result, schema=_SCHEMA)

    def get_intraday_status(self) -> dict:
        with self._stats_lock:
            return dict(self._intraday_stats)

    def _publish_stats(self, stats) -> None:
        with self._stats_lock:
            self._intraday_stats = dict(stats)

    def get_intraday_batch(self, symbols, count=300, asset_type="stock") -> pl.DataFrame:
        """Collect today's stock OHLC bars in 32-symbol blocks, never historical backfill.

        Empty/stale/error responses remain explicit coverage gaps. Valid blocks
        may be persisted, but the refresh service must not mark them complete.
        """
        if asset_type != "stock":
            raise ValueError("ELTDX全量分钟只采集股票; ETF仍使用单只按需查询")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 800:
            raise ValueError("ELTDX分钟页数量须为1到800的整数")
        symbols = list(dict.fromkeys(symbols))
        if len(symbols) > 10000:
            raise ValueError("ELTDX全量分钟标的数量超出采集预算")
        for symbol in symbols:
            wire_symbol(symbol)
        if not self._intraday_lock.acquire(blocking=False):
            raise EltdxGatewayError("ELTDX全量分钟正在采集, 本轮未重复启动")
        stats = {"requested_symbols": len(symbols), "covered_symbols": 0,
                 "empty_symbols": 0, "failed_symbols": 0,
                 "unqueried_symbols": len(symbols), "requests": 0,
                 "collecting": True, "collection_complete": False,
                 "oldest_latest_bar": None}
        frames = []
        try:
            self._publish_stats(stats)
            with self._client_lock:
                if self._client is None:
                    self._client = EltdxGatewayClient()
            start = cn_now().replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
            deadline = time.monotonic() + 600
            for offset in range(0, len(symbols), INTRADAY_CHUNK_SIZE):
                end = cn_now().replace(tzinfo=None)
                if end.date() != start.date() or time.monotonic() >= deadline:
                    break
                chunk = symbols[offset:offset + INTRADAY_CHUNK_SIZE]
                stats["requests"] += 1  # batch attempts; health probes are not data batches
                try:
                    rows_by_symbol = self._client.fetch_intraday_chunk(chunk, count=max(count, 300))
                except EltdxGatewayError:
                    stats["failed_symbols"] += len(chunk)
                else:
                    for symbol in chunk:
                        try:
                            frame = self._bars_frame(symbol, rows_by_symbol[symbol], start, end)
                        except (EltdxGatewayError, KeyError):
                            stats["failed_symbols"] += 1
                            continue
                        if frame.is_empty():
                            stats["empty_symbols"] += 1
                        else:
                            frames.append(frame)
                            stats["covered_symbols"] += 1
                            latest = frame["datetime"].max()
                            stats["oldest_latest_bar"] = min(stats["oldest_latest_bar"], latest) if stats["oldest_latest_bar"] else latest
                stats["unqueried_symbols"] -= len(chunk)
                self._publish_stats(stats)
            stats["collection_complete"] = (bool(symbols) and stats["covered_symbols"] == len(symbols))
            return pl.concat(frames) if frames else pl.DataFrame(schema=_SCHEMA)
        finally:
            stats["collecting"] = False
            self._publish_stats(stats)
            self._intraday_lock.release()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        """Read a recent page without persisting or claiming current-day freshness."""
        symbols = symbols or ["600519.SH"]
        if dataset == "minute":
            frame = self.get_minute(symbols, None, None)
        elif dataset == "full_minute":
            if len(set(symbols)) > INTRADAY_CHUNK_SIZE:
                raise ValueError("ELTDX全量分钟试拉最多32只; 不启动全市场采集")
            frame = self.get_intraday_batch(symbols)
        else:
            raise ValueError("ELTDX研究插件仅提供分钟K和股票全量分钟")
        return {
            "provider": self.name, "dataset": dataset,
            "rows": frame.height, "columns": frame.columns,
            "preview": frame.head(5).to_dicts(),
            "observed_start": frame["datetime"].min().isoformat() if frame.height else None,
            "observed_end": frame["datetime"].max().isoformat() if frame.height else None,
            "price_basis": "raw", "volume_unit": "手", "amount_available": False,
        }
