import type { GlobalMarketOverviewItem } from './types'
import { observationStats } from './overviewMetrics'

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
export const DISTRIBUTION_LABELS = ['<-5%', '-5~-3%', '-3~-1%', '-1~0%', '0~1%', '1~3%', '3~5%', '≥5%']
const boundaries = [-.05, -.03, -.01, 0, .01, .03, .05]

/** Actual observations only; proportions are coverage, never confidence scores. */
export function boardMetrics(items: GlobalMarketOverviewItem[], timezone: string) {
  const stats = observationStats(items, timezone)
  const values = stats.eligible.map(row => row.change_pct!).sort((a, b) => a - b)
  const bins = Array<number>(8).fill(0)
  for (const value of values) {
    const index = boundaries.findIndex(boundary => value < boundary)
    bins[index < 0 ? 7 : index] += 1
  }
  const technical = stats.eligible.filter(row => row.daily?.date === stats.date)
  const above = (key: 'above_ma5' | 'above_ma20' | 'above_ma60') => {
    const rows = technical.filter(row => typeof row.daily?.[key] === 'boolean')
    return { count: rows.filter(row => row.daily![key]).length, total: rows.length }
  }
  const highRows = technical.filter(row => typeof row.daily?.new_high_60 === 'boolean')
  const lowRows = technical.filter(row => typeof row.daily?.new_low_60 === 'boolean')
  const ratioRows = technical.filter(row => finite(row.daily?.volume_ratio_5))
  const n = values.length
  return { ...stats, bins, technical,
    mean: n ? values.reduce((a, b) => a + b, 0) / n : null,
    median: n ? (values[Math.floor((n - 1) / 2)] + values[Math.floor(n / 2)]) / 2 : null,
    strong: values.filter(value => value >= .03).length,
    weak: values.filter(value => value <= -.03).length,
    above5: above('above_ma5'), above20: above('above_ma20'), above60: above('above_ma60'),
    high: { count: highRows.filter(row => row.daily!.new_high_60).length, total: highRows.length },
    low: { count: lowRows.filter(row => row.daily!.new_low_60).length, total: lowRows.length },
    elevatedVolume: ratioRows.filter(row => row.daily!.volume_ratio_5! >= 1.5).length,
    volumeCovered: ratioRows.length,
  }
}

export type BoardRank = 'gainers' | 'losers' | 'momentum' | 'volume'
export function rankedObservations(items: GlobalMarketOverviewItem[], timezone: string, rank: BoardRank) {
  const stats = boardMetrics(items, timezone)
  const getValue = (row: GlobalMarketOverviewItem) => rank === 'volume' ? row.daily?.volume_ratio_5
    : rank === 'momentum' ? row.daily?.return_20d : row.change_pct
  const rows = rank === 'volume' || rank === 'momentum' ? stats.technical : stats.eligible
  return rows.filter(row => finite(getValue(row))).sort((a, b) =>
    (rank === 'losers' ? 1 : -1) * (getValue(a)! - getValue(b)!) || a.symbol.localeCompare(b.symbol)).slice(0, 8)
}
