import type { KlineRow } from './api'
import type { OHLC } from '@/components/EChartsCandlestick'
import { normalizeTradingViewDailyRows } from '@/components/tradingview-daily'

const indicators = ['macd_dif', 'macd_dea', 'macd_hist', 'rsi_6', 'rsi_14', 'rsi_24', 'kdj_k', 'kdj_d', 'kdj_j', 'boll_upper', 'boll_lower', 'atr_14', 'adx_14', 'adx_plus_di_14', 'adx_minus_di_14'] as const

/** Both daily chart renderers share date, price, duplicate and volume validation. */
export function dailyChartRows(rows: readonly KlineRow[]): OHLC[] {
  const grouped = new Map<string, KlineRow[]>()
  for (const row of rows) {
    if (!row || typeof row.date !== 'string') continue
    const group = grouped.get(row.date) ?? []
    group.push(row)
    grouped.set(row.date, group)
  }
  return normalizeTradingViewDailyRows(rows).bars.map(bar => {
    const extra: Partial<OHLC> = {}
    const originals = grouped.get(bar.date) ?? []
    for (const key of indicators) {
      const values = originals.map(row => row[key]).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
      extra[key] = values.length && values.every(value => value === values[0]) ? values[0] : null
    }
    return { ...bar, ...extra, is_live: originals.some(row => row.is_live === true) }
  })
}

export function volumeAverageAt(data: readonly { volume?: number | null }[], index: number, window: number): number | null {
  if (index < window - 1) return null
  let total = 0
  for (let i = index - window + 1; i <= index; i++) {
    const value = data[i]?.volume
    if (value == null || !Number.isFinite(value) || value < 0) return null
    total += value
  }
  return total / window
}
