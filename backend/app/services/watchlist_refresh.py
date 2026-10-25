# ruff: noqa: RUF001 -- Native Chinese status messages.
"""Bounded, explicit ETF history repair using the existing pipeline write slot."""
from contextlib import nullcontext
from datetime import timedelta

import polars as pl

from app.market_time import cn_now
from app.services import index_sync, watchlist
from app.services.pipeline_jobs import (
    job_store,
    release_run_slot,
    run_with_capacity,
    try_acquire_run_slot,
)


class WatchlistRefreshBusyError(RuntimeError):
    pass


class WatchlistRefreshLimitError(ValueError):
    pass


def missing_etfs(repo, symbols: list[str]) -> list[str]:
    if not symbols:
        return []

    frame, _ = repo.get_enriched_latest_asset('etf')
    if frame.is_empty() or not {'symbol', 'close'} <= set(frame.columns):
        return list(symbols)

    close = pl.col('close').cast(pl.Float64, strict=False)
    ready = set(frame.filter(close.is_finite() & (close > 0))['symbol'].to_list())
    return [symbol for symbol in symbols if symbol not in ready]


def sync_etfs(repo, capset, quote_service=None, *, force: bool = False) -> dict:
    entries = watchlist.list_symbols()
    etfs = repo.get_etf_symbol_set()
    symbols = list(dict.fromkeys(row['symbol'] for row in entries if row['symbol'] in etfs))
    requested = symbols if force else missing_etfs(repo, symbols)
    if len(requested) > 60:
        raise WatchlistRefreshLimitError('单次最多补拉60只自选ETF')
    if not requested:
        return {'requested': [], 'rows_written': 0, 'remaining': [], 'as_of': None}

    job_id, created = job_store.create()
    if not created:
        raise WatchlistRefreshBusyError('已有数据同步任务，请完成后再刷新自选')
    if not try_acquire_run_slot(job_id):
        job_store.fail(job_id, '已有数据写入正在运行')
        raise WatchlistRefreshBusyError('已有数据写入正在运行，请稍后重试')

    try:
        def run():
            # Only market history/indicators are touched, never the full daily
            # pipeline that also settles paper accounts and generates reports.
            now = cn_now().replace(tzinfo=None)

            def progress(current, total):
                job_store.progress(
                    job_id,
                    'watchlist_etf',
                    int(90 * current / max(total, 1)),
                    f'自选ETF历史 {current}/{total}',
                )

            pause = quote_service.paused() if quote_service else nullcontext()
            with pause:
                warnings = ['历史指标使用当前可用的复权因子；缺失因子的区间按原始价格计算。']
                try:
                    index_sync.sync_etf_adj_factor(
                        requested,
                        repo,
                        capset,
                        start_time=now - timedelta(days=365),
                        end_time=now,
                    )
                except Exception:
                    warnings.insert(0, '本次ETF除权因子更新未完成，沿用已有因子。')

                written = index_sync.sync_and_persist_etf_daily(
                    repo,
                    capset,
                    start_date=now - timedelta(days=365),
                    end_date=now,
                    symbols_override=requested,
                    on_chunk_done=progress,
                )
                if written:
                    repo.refresh_etf_enriched()
                remaining = missing_etfs(repo, requested)
                _, as_of = repo.get_enriched_latest_asset('etf')

            return {
                'requested': requested,
                'rows_written': written,
                'remaining': remaining,
                'as_of': str(as_of) if as_of else None,
                'job_id': job_id,
                'warnings': warnings,
            }

        result = run_with_capacity(job_id, run)
        if not result['rows_written']:
            raise RuntimeError('本次未取得ETF日线，原有数据已保留')
        job_store.succeed(job_id, result)
        return result
    except Exception:
        job_store.fail(job_id, '自选ETF日线补拉未完成，原有数据保留')
        raise
    finally:
        release_run_slot(job_id)
