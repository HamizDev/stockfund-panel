"""Single-security, raw minute bars from an external research gateway."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from threading import Lock

import polars as pl

from .client import (
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
    datasets: dict = field(default_factory=lambda: {"minute": None})
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

    def __init__(self) -> None:
        self.config = _Config()
        self._client = None
        self._client_lock = Lock()

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
        if on_chunk_done is not None:
            on_chunk_done(1, 1)
        return pl.DataFrame(result, schema=_SCHEMA)

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        """Read a recent page without persisting or claiming current-day freshness."""
        if dataset != "minute":
            raise ValueError("ELTDX研究插件仅提供单只分钟K试拉")
        frame = self.get_minute(symbols or ["600519.SH"], None, None)
        return {
            "provider": self.name, "dataset": dataset,
            "rows": frame.height, "columns": frame.columns,
            "preview": frame.head(5).to_dicts(),
            "observed_start": frame["datetime"].min().isoformat() if frame.height else None,
            "observed_end": frame["datetime"].max().isoformat() if frame.height else None,
            "price_basis": "raw", "volume_unit": "手", "amount_available": False,
        }
