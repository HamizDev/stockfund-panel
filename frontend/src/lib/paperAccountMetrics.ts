import type { PaperNavItem } from './api'
import { isDailyChartDate } from '@/components/tradingview-daily'

export const finiteMoney = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)

/** Only explicitly complete, balanced same-day snapshots may be charted as equity. */
export function completeSnapshot(point: PaperNavItem): boolean {
  return point.valuation_complete === true && isDailyChartDate(point.date)
    && [point.cash, point.mv, point.nav].every(finiteMoney) && point.nav > 0
    && Math.abs(point.cash + point.mv - point.nav) <= .02
}

export function paperAccountMetrics(input: PaperNavItem[]) {
  const counts = new Map<string, number>()
  input.forEach(point => counts.set(point.date, (counts.get(point.date) ?? 0) + 1))
  const points = [...input].sort((a, b) => a.date.localeCompare(b.date))
  let peak = 0
  const series = points.map(point => {
    const valid = counts.get(point.date) === 1 && completeSnapshot(point)
    if (!valid) return { ...point, valid: false, equity: null, drawdown: null }
    peak = Math.max(peak, point.nav)
    return { ...point, valid: true, equity: point.nav, drawdown: (point.nav / peak - 1) * 100 }
  })
  const complete = series.filter(point => point.valid)
  const latest = complete.at(-1) ?? null
  const fullyVerified = series.length > 0 && complete.length === series.length
  return {
    series, latest, verifiedCount: complete.length, totalCount: series.length,
    maxDrawdown: fullyVerified ? Math.max(0, ...complete.map(point => -(point.drawdown ?? 0))) : null,
    latestIsCurrent: latest !== null && latest === series.at(-1),
  }
}
