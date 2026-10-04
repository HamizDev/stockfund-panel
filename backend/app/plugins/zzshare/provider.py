"""zzshare single-stock 1-minute provider for the unified custom data route."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import polars as pl

from app.plugins.zzshare import client as zzshare_client
from app.plugins.zzshare.client import ZZShareClient

logger = logging.getLogger(__name__)

_DATASETS = ("minute",)
_SYMBOL_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_MINUTE_SCHEMA = {
    "symbol": pl.String,
    "datetime": pl.Datetime("us"),
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
}
_MINUTE_COLS = list(_MINUTE_SCHEMA)


def availability() -> tuple[bool, str]:
    """Dependency-only loader check; no network request is made."""
    try:
        import httpx  # noqa: F401

        return True, "ok"
    except ImportError as exc:
        return False, f"缺少依赖 httpx: {exc}"


@dataclass
class _ZZShareConfig:
    """Config shim consumed by the built-in plugin loader."""

    name: str = "zzshare"
    display_name: str = "zzshare"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


def _empty_minute_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=_MINUTE_SCHEMA)


def _finite_number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _parse_trade_time(value) -> datetime | None:
    raw = str(value or "")
    for fmt in ("%Y%m%d%H%M", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def _wallclock(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    from zoneinfo import ZoneInfo

    return value.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


def _normalize_rows(
    rows: list[dict],
    symbol: str,
    start_time: datetime | None,
    end_time: datetime | None,
) -> pl.DataFrame:
    """Map only exact-code, valid OHLC rows into the internal minute contract."""
    start = _wallclock(start_time)
    end = _wallclock(end_time)
    records: list[dict] = []
    for row in rows:
        if row.get("code") != symbol:
            logger.warning("zzshare minute 跳过代码不匹配行: requested=%s", symbol)
            continue
        dt = _parse_trade_time(row.get("trade_time"))
        if dt is None or (start is not None and dt < start) or (end is not None and dt > end):
            continue

        open_ = _finite_number(row.get("open"))
        high = _finite_number(row.get("high"))
        low = _finite_number(row.get("low"))
        close = _finite_number(row.get("close"))
        volume_shares = _finite_number(row.get("vol"))
        amount_raw = row.get("amount")
        amount = _finite_number(amount_raw) if amount_raw is not None else None
        if (
            None in (open_, high, low, close, volume_shares)
            or open_ <= 0 or high <= 0 or low <= 0 or close <= 0
            or volume_shares < 0
            or (amount_raw is not None and (amount is None or amount < 0))
            or low > min(open_, close)
            or high < max(open_, close)
        ):
            continue

        records.append(
            {
                "symbol": symbol,
                "datetime": dt,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume_shares / 100.0,
                "amount": amount,
            }
        )
    if not records:
        return _empty_minute_frame()
    return (
        pl.DataFrame(records, schema=_MINUTE_SCHEMA)
        .unique(subset=["symbol", "datetime"], keep="last")
        .sort(["symbol", "datetime"])
    )


class ZZShareProvider:
    """Anonymous zzshare adapter; stock-only, single-symbol minute queries."""

    name = "zzshare"
    builtin = True
    minute_asset_types = ("stock",)
    minute_price_basis = "unverified"
    minute_max_symbols_per_request = 30

    def __init__(self) -> None:
        self.config = _ZZShareConfig()
        self._client: ZZShareClient | None = None

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                logger.debug("zzshare client close failed", exc_info=True)
            self._client = None

    def _get_client(self) -> ZZShareClient:
        if self._client is None:
            self._client = zzshare_client.ZZShareClient()
        return self._client

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        freq: str = "1m",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        """Fetch single-stock 1-minute bars; no ETF/index/realtime/full-market support."""
        if not symbols:
            return _empty_minute_frame()
        symbols = list(dict.fromkeys(symbols))
        if len(symbols) > self.minute_max_symbols_per_request:
            raise ValueError("zzshare 匿名分钟仅支持每批最多 30 只候选, 不支持全市场同步")
        if asset_type != "stock":
            raise ValueError(f"zzshare 只支持股票分钟: 不支持 asset_type={asset_type!r}")
        if str(freq or "1m").strip().lower() not in {"1m", "1min"}:
            raise ValueError(f"zzshare 只支持 1m 分钟K: 不支持 freq={freq!r}")
        invalid = [symbol for symbol in symbols if not _SYMBOL_RE.fullmatch(symbol)]
        if invalid:
            raise ValueError("zzshare 证券代码格式无效")
        if (start_time is None) != (end_time is None):
            raise ValueError("zzshare 区间查询要求同时提供 start_time 和 end_time")

        frames: list[pl.DataFrame] = []
        client = self._get_client()
        for index, symbol in enumerate(symbols):
            try:
                rows = client.fetch_minute(symbol, start_time, end_time)
                frame = _normalize_rows(rows, symbol, start_time, end_time)
                if not frame.is_empty():
                    frames.append(frame)
            except zzshare_client.ZZShareError as exc:
                logger.warning("zzshare minute 拉取失败: %s", exc)
                raise
            finally:
                if on_chunk_done is not None:
                    on_chunk_done(index + 1, len(symbols))

        if not frames:
            return _empty_minute_frame()
        return (
            pl.concat(frames, how="vertical")
            .unique(subset=["symbol", "datetime"], keep="last")
            .sort(["symbol", "datetime"])
        )
