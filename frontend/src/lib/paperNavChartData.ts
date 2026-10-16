import type { PaperNavItem } from './api'
import { finiteMoney, paperAccountMetrics } from './paperAccountMetrics'

export function paperNavChartData(nav: PaperNavItem[]) {
  const metrics = paperAccountMetrics(nav)
  const first = metrics.series.find(point => point.valid)
  const base = first?.equity
  const benchmarkBase = first?.benchmark_close
  const hasBenchmark = finiteMoney(benchmarkBase) && benchmarkBase > 0
  return {
    ...metrics, hasBenchmark,
    dates: metrics.series.map(point => point.date),
    equity: metrics.series.map(point => point.equity != null && base != null ? point.equity / base * 100 : null),
    benchmark: metrics.series.map(point => point.valid && hasBenchmark && finiteMoney(point.benchmark_close) && point.benchmark_close > 0 ? point.benchmark_close / benchmarkBase * 100 : null),
  }
}

/** Performance comparisons require every snapshot in the selected series to be complete. */
export function verifiedPaperPerformance(nav: PaperNavItem[]) {
  const metrics = paperAccountMetrics(nav)
  const usable = metrics.totalCount >= 2 && metrics.verifiedCount === metrics.totalCount
  const ratio = usable ? metrics.latest!.nav / metrics.series[0].nav : null
  const annual = ratio !== null ? (Math.pow(ratio, 252 / metrics.totalCount) - 1) * 100 : null
  return {
    total_return_pct: ratio !== null ? (ratio - 1) * 100 : null,
    annual_pct: annual !== null && Number.isFinite(annual) ? annual : null,
    max_drawdown: metrics.maxDrawdown,
  }
}
