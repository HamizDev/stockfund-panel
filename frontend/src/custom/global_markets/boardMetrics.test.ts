import { describe, expect, it } from 'vitest'
import { boardMetrics, DISTRIBUTION_LABELS, rankedObservations } from './boardMetrics'
import type { GlobalDailyTechnicals, GlobalMarketOverviewItem } from './types'

const quoteTime = (iso: string) => Date.parse(iso)

function daily(overrides: Partial<GlobalDailyTechnicals> = {}): GlobalDailyTechnicals {
  return {
    date: '2026-10-16', bars_count: 61,
    ma5: 10, ma20: 9, ma60: 8,
    above_ma5: null, above_ma20: null, above_ma60: null,
    return_5d: null, return_20d: null, volume_ratio_5: null,
    new_high_60: null, new_low_60: null, range_position_60: null,
    ...overrides,
  }
}

function item(overrides: Partial<GlobalMarketOverviewItem> = {}): GlobalMarketOverviewItem {
  return {
    symbol: 'NVDA', name: 'NVIDIA', market: 'us', kind: 'stock',
    price: 100, change_pct: 0.01,
    quote_at_ms: quoteTime('2026-10-16T12:00:00Z'),
    fetched_at_ms: quoteTime('2026-10-16T12:01:00Z'),
    stale: false, error: null, status: 'ok',
    ...overrides,
  }
}

describe('global board metrics', () => {
  it('uses same-local-date observations and measures technical coverage only where daily dates match', () => {
    const rows = [
      item({ symbol: 'A', change_pct: 0.06, quote_at_ms: quoteTime('2026-10-16T01:00:00Z'),
        daily: daily({ above_ma5: true, above_ma20: false, above_ma60: null, new_high_60: true,
          new_low_60: false, volume_ratio_5: 1.5, return_20d: 0.04 }) }),
      // 18:00 UTC is already the next local day in Hong Kong.
      item({ symbol: 'B', change_pct: 0, quote_at_ms: quoteTime('2026-10-15T18:00:00Z'),
        daily: daily({ above_ma5: false, above_ma20: true, above_ma60: false, new_high_60: false,
          new_low_60: false, volume_ratio_5: 1.49, return_20d: -0.1 }) }),
      item({ symbol: 'C', change_pct: -0.04, quote_at_ms: quoteTime('2026-10-16T04:00:00Z'),
        daily: daily({ date: '2026-10-15', above_ma5: true, above_ma20: true, above_ma60: true,
          new_high_60: true, new_low_60: true, volume_ratio_5: 4, return_20d: 0.5 }) }),
      item({ symbol: 'OLD', quote_at_ms: quoteTime('2026-10-15T15:59:00Z') }),
      item({ symbol: 'NO_PRICE', price: null }),
      item({ symbol: 'STALE', stale: true }),
      item({ symbol: 'FAILED', status: 'error' }),
      item({ symbol: '^GSPC', kind: 'index', change_pct: 0.02 }),
    ]

    const metrics = boardMetrics(rows, 'Asia/Hong_Kong')

    expect(metrics.date).toBe('2026-10-16')
    expect(metrics.total).toBe(7)
    expect(metrics.covered).toBe(3)
    expect(metrics.indices).toBe(1)
    expect(metrics.indexCovered).toBe(1)
    expect(metrics.bins).toEqual([0, 1, 0, 0, 1, 0, 0, 1])
    expect(metrics.mean).toBeCloseTo((0.06 + 0 - 0.04) / 3)
    expect(metrics.median).toBe(0)
    expect(metrics.strong).toBe(1)
    expect(metrics.weak).toBe(1)
    expect(metrics.above5).toEqual({ count: 1, total: 2 })
    expect(metrics.above20).toEqual({ count: 1, total: 2 })
    expect(metrics.above60).toEqual({ count: 0, total: 1 })
    expect(metrics.high).toEqual({ count: 1, total: 2 })
    expect(metrics.low).toEqual({ count: 0, total: 2 })
    expect(metrics.elevatedVolume).toBe(1)
    expect(metrics.volumeCovered).toBe(2)
    expect(metrics.technical.map(row => row.symbol)).toEqual(['A', 'B'])
    expect(DISTRIBUTION_LABELS).toHaveLength(8)
  })

  it('buckets decimal percentage changes at the documented boundaries', () => {
    const changes = [-0.05, -0.03, -0.01, 0, 0.01, 0.03, 0.05]
    const metrics = boardMetrics(changes.map((change_pct, index) => item({
      symbol: `B${index}`, change_pct,
    })), 'America/New_York')

    expect(metrics.bins).toEqual([0, 1, 1, 1, 1, 1, 1, 1])
    expect(metrics.strong).toBe(2)
    expect(metrics.weak).toBe(2)
  })

  it('ranks valid market changes separately from same-day technical momentum and volume', () => {
    const rows = [
      item({ symbol: 'A', change_pct: 0.04, daily: daily({ return_20d: 0.12, volume_ratio_5: 2 }) }),
      item({ symbol: 'B', change_pct: 0.02, daily: daily({ return_20d: -0.03, volume_ratio_5: 1.2 }) }),
      item({ symbol: 'C', change_pct: -0.01, daily: daily({ date: '2026-10-15', return_20d: 0.9, volume_ratio_5: 9 }) }),
      item({ symbol: 'MISSING', change_pct: null, daily: daily({ return_20d: 0.99, volume_ratio_5: 99 }) }),
    ]

    expect(rankedObservations(rows, 'America/New_York', 'gainers').map(row => row.symbol)).toEqual(['A', 'B', 'C'])
    expect(rankedObservations(rows, 'America/New_York', 'momentum').map(row => row.symbol)).toEqual(['A', 'B'])
    expect(rankedObservations(rows, 'America/New_York', 'volume').map(row => row.symbol)).toEqual(['A', 'B'])
    expect(rankedObservations(rows, 'America/New_York', 'losers').map(row => row.symbol)).toEqual(['C', 'B', 'A'])
  })
})
