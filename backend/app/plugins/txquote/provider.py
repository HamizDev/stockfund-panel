"""txquote 数据源 provider: 分钟K (新浪/腾讯) + 五档盘口 (腾讯)。

方法签名对齐 custom.GenericHTTPProvider(service 分流点按这套签名调用),
注入 custom loader 注册表后, 各 service 无需改动即可路由到本 provider。

实现数据集:
  - minute       分钟K → 内部 minute canonical (symbol/datetime/open/high/low/
                 close/volume/amount)。freq 映射: 1m 用腾讯近 5 日 1 分钟K
                 (day/query, 按请求窗口过滤); 5m/15m/30m/60m 用新浪 K 线;
                 其余抛错。
  - depth5       五档盘口 → {symbol: {bid_prices/bid_volumes/ask_prices/
                 ask_volumes/last_price/timestamp}}。腾讯 q= 批量接口。
未声明其他数据集 → provider_has_dataset 为 False, 自动回退 tickflow/fuyao。

单位与口径 (CONTRIBUTING §3.1, 不可凭字段名推断):
  - 腾讯 q=/分时/5日分时 volume 已是手, 直接透传 (金额=价x量x100 反推验证);
    新浪 K 线 volume 为股 → floor(/100) 转手。
  - amount 均为元, 直接透传。
  - datetime 为北京时间墙钟 (naive), 与分钟K契约一致。
  - 缺字段/解析失败的行跳过, 不伪造数据; 网络失败软返回空, 不阻断上游。
"""

from __future__ import annotations

import contextlib
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import polars as pl

from app.plugins.txquote import client as tx_client
from app.plugins.txquote.client import TxQuoteClient, TxQuoteError

logger = logging.getLogger(__name__)

# 只声明真实提供的数据集; 其余数据集 provider_has_dataset 返回 False → 回退
_DATASETS = ("minute", "depth5")

# freq → 新浪 scale 映射。1m 走腾讯分时 (仅当日), 不在此表。
_FREQ_TO_SINA_SCALE = {"5m": 5, "15m": 15, "30m": 30, "60m": 60}
_MINUTE_BATCH = 20  # 新浪为单标的接口, 逐只拉取; 批量节流
_MINUTE_INTERVAL_S = 0.3  # 单标的请求间隔 (礼貌节流)
_DEPTH_BATCH = 60  # 腾讯 q= 单次批量上限 (与 client 内部分片一致)

_MINUTE_COLS = ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]


def availability() -> tuple[bool, str]:
    """loader 启动自检: 无需 Key, 依赖 httpx (后端已有)。不抛异常。"""
    try:
        import httpx  # noqa: F401

        return True, "ok"
    except ImportError as e:
        return False, f"缺少依赖 httpx: {e}"


@dataclass
class _TxQuoteConfig:
    """轻量 config shim, 让 custom loader 的 provider_has_dataset 能识别本 provider。"""

    name: str = "txquote"
    display_name: str = "txquote"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TxQuoteProvider:
    """txquote 数据源。minute = 分钟K; depth5 = 五档盘口。"""

    name = "txquote"
    builtin = True
    minute_asset_types = ("stock",)
    # 1m 近 5 交易日 (腾讯 day/query); 5m+ 约 20 交易日。声明浅历史, 分时档位自动收窄。
    minute_history_days = 5

    def __init__(self) -> None:
        self.config = _TxQuoteConfig()
        self._client: TxQuoteClient | None = None

    def close(self) -> None:  # loader.load_all 重建注册表时会对每个 provider 调 close
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None

    def _get_client(self) -> TxQuoteClient:
        if self._client is None:
            self._client = tx_client.TxQuoteClient()
        return self._client

    # ---- depth5 ----
    def get_depth_batch(self, symbols: list[str]) -> dict[str, dict]:
        """批量取五档盘口 → {symbol: depth record}。

        record: bid_prices/bid_volumes/ask_prices/ask_volumes (各 5 个, 量为手),
        last_price, timestamp (ms epoch, 北京)。单只失败跳过; 整批网络失败
        软返回空 dict (不阻断 depth_service)。
        """
        if not symbols:
            return {}
        try:
            return self._get_client().depth_batch(symbols)
        except TxQuoteError as e:
            logger.warning("txquote 五档盘口拉取失败: %s", e)
            return {}

    # ---- minute ----
    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        freq: str = "1m",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        """分钟K → 内部 minute canonical DataFrame。

        freq 映射: "1m" 用腾讯近 5 日 1 分钟K (day/query, 按请求窗口过滤);
        "5m"/"15m"/"30m"/"60m" 用新浪 K 线; 其他 freq 抛 ValueError 明示。
        网络/解析失败软返回空 DataFrame (上游会回退 TickFlow)。
        """
        if not symbols or asset_type != "stock":
            return pl.DataFrame()
        freq = (freq or "1m").strip().lower()
        if freq == "1m":
            frames = self._minute_1m(symbols, start_time, end_time, on_chunk_done)
        elif freq in _FREQ_TO_SINA_SCALE:
            frames = self._minute_sina(
                symbols, start_time, end_time, _FREQ_TO_SINA_SCALE[freq], on_chunk_done
            )
        else:
            raise ValueError(
                f"txquote 不支持 freq={freq!r}, 仅支持 1m/5m/15m/30m/60m"
            )
        non_empty = [df for df in frames if not df.is_empty()]
        if not non_empty:
            return pl.DataFrame()
        return pl.concat(non_empty, how="diagonal_relaxed").sort(["symbol", "datetime"])

    def _minute_1m(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        on_chunk_done: Callable[[int, int], None] | None,
    ) -> list[pl.DataFrame]:
        """1m: 腾讯近 5 日 1 分钟K → 1m K 线 (open=high=low=close=该分钟价)。

        day/query 接口返回最近 5 个交易日的数据; 按数据实际日期与请求窗口
        过滤, 不在窗口内返回空 (不伪造)。volume 已是手, 直接透传。
        """
        from datetime import date

        start_d = start_time.date() if start_time else None
        end_d = end_time.date() if end_time else None
        # 请求窗口完全在未来 → 无数据
        if start_d is not None and start_d > date.today():
            return []
        client = self._get_client()
        frames: list[pl.DataFrame] = []
        for i, sym in enumerate(symbols):
            if i:
                time.sleep(_MINUTE_INTERVAL_S)
            try:
                days = client.fetch_day_minute(sym)
            except TxQuoteError as e:
                logger.warning("txquote 1m %s 失败: %s", sym, e)
                continue
            recs = []
            for date_iso, rows in days:
                try:
                    data_d = date.fromisoformat(date_iso)
                except ValueError:
                    continue
                # 数据日期不在请求窗口内 → 跳过 (不混入)
                if start_d is not None and data_d < start_d:
                    continue
                if end_d is not None and data_d > end_d:
                    continue
                for r in rows:
                    parts = r.split()
                    if len(parts) < 4:
                        continue
                    hhmm, price_s, vol_s, amt_s = parts[0], parts[1], parts[2], parts[3]
                    price = _to_float(price_s)
                    vol = _to_float(vol_s)  # 手 (源已是手, 金额反推验证)
                    amt = _to_float(amt_s)  # 元
                    if price is None or len(hhmm) != 4 or not hhmm.isdigit():
                        continue
                    dt = f"{date_iso} {hhmm[:2]}:{hhmm[2:]}:00"
                    recs.append(
                        {
                            "symbol": sym,
                            "datetime": dt,
                            "open": price,
                            "high": price,
                            "low": price,
                            "close": price,
                            "volume": vol,
                            "amount": amt,
                        }
                    )
            if recs:
                frames.append(_normalize_minute_frame(recs))
            if on_chunk_done:
                on_chunk_done(i + 1, len(symbols))
        return frames

    def _minute_sina(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        scale: int,
        on_chunk_done: Callable[[int, int], None] | None,
    ) -> list[pl.DataFrame]:
        """5/15/30/60m: 新浪 K 线。volume 股→手 (floor /100), amount 元透传。"""
        # datalen 估算: 窗口天数 x 每日根数 (5m:48/天, 15m:16/天, 30m:8/天, 60m:4/天)
        per_day = {5: 48, 15: 16, 30: 8, 60: 4}[scale]
        if start_time is not None and end_time is not None:
            days = max(1, (end_time.date() - start_time.date()).days + 1)
        else:
            days = 30
        datalen = min(1000, max(50, days * per_day))
        start_d = start_time.date() if start_time else None
        end_d = end_time.date() if end_time else None
        client = self._get_client()
        frames: list[pl.DataFrame] = []
        for i, sym in enumerate(symbols):
            if i:
                time.sleep(_MINUTE_INTERVAL_S)
            try:
                bars = client.minute_kline(sym, scale, datalen=datalen)
            except TxQuoteError as e:
                logger.warning("txquote %s %s 失败: %s", sym, f"{scale}m", e)
                continue
            recs = []
            for b in bars:
                day_s = b.get("day")
                if not day_s:
                    continue
                try:
                    bar_date = datetime.strptime(day_s[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                if start_d is not None and bar_date < start_d:
                    continue
                if end_d is not None and bar_date > end_d:
                    continue
                v = _to_float(b.get("volume"))  # 股
                recs.append(
                    {
                        "symbol": sym,
                        "datetime": day_s[:19] if len(day_s) >= 19 else day_s,
                        "open": _to_float(b.get("open")),
                        "high": _to_float(b.get("high")),
                        "low": _to_float(b.get("low")),
                        "close": _to_float(b.get("close")),
                        "volume": math.floor(v / 100.0) if v is not None else None,
                        "amount": _to_float(b.get("amount")),
                    }
                )
            if recs:
                frames.append(_normalize_minute_frame(recs))
            if on_chunk_done:
                on_chunk_done(i + 1, len(symbols))
        return frames


def _normalize_minute_frame(recs: list[dict]) -> pl.DataFrame:
    """分钟记录 → minute canonical (列顺序/类型对齐 custom._normalize_minute)。"""
    df = pl.DataFrame(recs)
    if df.is_empty():
        return df
    # datetime 字符串 → Datetime(us, naive=北京时间墙钟)
    if df.schema.get("datetime") == pl.Utf8:
        df = df.with_columns(
            pl.col("datetime").str.to_datetime(strict=False).alias("datetime")
        )
    df = df.with_columns(pl.col("datetime").cast(pl.Datetime("us"), strict=False))
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))
    keep = [c for c in _MINUTE_COLS if c in df.columns]
    out = df.select(keep) if keep else pl.DataFrame()
    return out.drop_nulls(subset=["datetime"])
