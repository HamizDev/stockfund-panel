"""Read index points independently of stock/ETF minute routing, without persistence."""
from datetime import date, datetime

import polars as pl

from app.market_time import CN_TZ
from app.services import kline_sync

PROVIDER = "txquote"
HISTORY_DAYS = 5


class IndexMinuteUnavailableError(RuntimeError):
    """A failed source read must not be presented as a successful empty day."""


def fetch_index_minute(symbol: str, day: date) -> pl.DataFrame:
    try:
        provider, fallback, _error = kline_sync._resolve_minute_provider(PROVIDER, asset_type="index")
        if fallback or provider is None:
            raise IndexMinuteUnavailableError("腾讯指数分时数据源不可用")
        frame = provider.get_minute(
            [symbol], datetime(day.year, day.month, day.day, 9, 25, tzinfo=CN_TZ),
            datetime(day.year, day.month, day.day, 15, 5, tzinfo=CN_TZ),
            asset_type="index", freq="1m",
        )
        frame = kline_sync._enforce_minute_beijing_wallclock(frame, source=PROVIDER)
        if not frame.is_empty():
            valid = frame.filter(
                (pl.col("symbol") == symbol) & (pl.col("datetime").dt.date() == day)
                & pl.col("close").is_finite() & (pl.col("close") > 0)
            )
            if valid.height != frame.height:
                raise IndexMinuteUnavailableError("指数分时返回了错误的标的、日期或点位")
        return frame
    except Exception:
        raise IndexMinuteUnavailableError("腾讯指数分时暂时无法连接, 请稍后重试") from None
