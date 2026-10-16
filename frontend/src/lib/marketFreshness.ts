type QuoteStatus = {
  enabled?: boolean; running?: boolean; paused?: boolean; is_trading_hours?: boolean
  quote_age_ms?: number | null; interval_s?: number; mode?: string; market_phase?: string
}

/** A running polling service alone does not establish a fresh market quote. */
export function marketFreshness(status: QuoteStatus, historical: boolean) {
  if (historical) return { label: '历史快照', live: false, note: '当前查看所选日期的数据' }
  if (status.is_trading_hours === false) {
    return { label: '休市快照', live: false, note: '非连续交易时段，显示最近可用数据；日期以页面标注为准' }
  }
  if (!status.enabled || !status.running || status.paused) {
    return { label: '缓存行情', live: false, note: '行情服务未运行或已暂停，显示最近缓存' }
  }
  if (status.is_trading_hours !== true || status.quote_age_ms == null || !Number.isFinite(status.quote_age_ms) || status.quote_age_ms < 0) {
    return { label: '时间待核对', live: false, note: '未取得可核对的行情更新时间，不能确认实时性' }
  }
  const interval = typeof status.interval_s === 'number' && Number.isFinite(status.interval_s) && status.interval_s > 0 ? status.interval_s : 60
  if (status.quote_age_ms > Math.max(120_000, interval * 3_000)) {
    return { label: '行情延迟', live: false, note: '报价时间已超过更新周期，请检查数据源连接' }
  }
  if (status.mode === 'watchlist') return { label: '局部实时', live: true, note: '仅部分标的实时更新，其余数据可能来自盘后缓存' }
  if (status.mode !== 'full_market') return { label: '缓存行情', live: false, note: '未确认实时覆盖模式，显示最近可用数据' }
  return { label: '实时行情', live: true, note: '处于连续交易时段，报价时间在更新周期内' }
}
