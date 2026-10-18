import { describe, expect, it } from 'vitest'
import { formatGlobalMarketChange } from './marketFormatters'
import { changeClass, observationStats } from './overviewMetrics'
import type { GlobalMarketOverviewItem } from './types'

const quoteTime = (iso: string) => Date.parse(iso)

function item(overrides: Partial<GlobalMarketOverviewItem> = {}): GlobalMarketOverviewItem {
  return {
    symbol: '0700.HK',
    name: 'Test instrument',
    market: 'hk',
    kind: 'stock',
    price: 100,
    change_pct: 0.01,
    quote_at_ms: quoteTime('2026-10-18T01:00:00Z'),
    fetched_at_ms: quoteTime('2026-10-18T01:01:00Z'),
    stale: false,
    error: null,
    status: 'ok',
    ...overrides,
  }
}

describe('global market overview metrics', () => {
  it('counts only the supplied stock/ETF observation pool and keeps indices separate', () => {
    const stats = observationStats([
      item({ symbol: '0700.HK' }),
      item({ symbol: '2800.HK', kind: 'etf' }),
      item({ symbol: '^HSI', kind: 'index' }),
    ], 'Asia/Hong_Kong')

    expect(stats.total).toBe(2)
    expect(stats.covered).toBe(2)
    expect(stats.indices).toBe(1)
    expect(stats.indexCovered).toBe(1)
  })

  it('counts same-local-day fresh rows only, excluding old cache, failed and missing values', () => {
    const stats = observationStats([
      item({ symbol: '0700.HK', change_pct: 0.0366, quote_at_ms: quoteTime('2026-10-18T01:00:00Z') }),
      item({ symbol: '2800.HK', kind: 'etf', change_pct: 0, quote_at_ms: quoteTime('2026-10-18T09:00:00Z') }),
      item({ symbol: '0005.HK', change_pct: -0.01, quote_at_ms: quoteTime('2026-10-18T05:00:00Z') }),
      item({ symbol: 'OLD.HK', quote_at_ms: quoteTime('2026-10-17T15:59:00Z') }),
      item({ symbol: 'STALE.HK', quote_at_ms: quoteTime('2026-10-18T18:00:00Z'), stale: true }),
      item({ symbol: 'ERROR.HK', quote_at_ms: quoteTime('2026-10-18T04:00:00Z'), status: 'error' }),
      item({ symbol: 'NO-PRICE.HK', quote_at_ms: quoteTime('2026-10-18T04:00:00Z'), price: null }),
      item({ symbol: 'NO-CHANGE.HK', quote_at_ms: quoteTime('2026-10-18T04:00:00Z'), change_pct: null }),
      item({ symbol: 'NON-FINITE.HK', quote_at_ms: quoteTime('2026-10-18T04:00:00Z'), change_pct: Number.NaN }),
      item({ symbol: 'NO-TIME.HK', quote_at_ms: null }),
      item({ symbol: '^HSI', kind: 'index', change_pct: 0.005 }),
      item({ symbol: '^HSCE', kind: 'index', stale: true }),
    ], 'Asia/Hong_Kong')

    expect(stats.date).toBe('2026-10-18')
    expect(stats.total).toBe(10)
    expect(stats.covered).toBe(3)
    expect(stats.up).toBe(1)
    expect(stats.flat).toBe(1)
    expect(stats.down).toBe(1)
    expect(changeClass(0)).toBe('text-muted')
    expect(stats.indices).toBe(2)
    expect(stats.indexCovered).toBe(1)
    expect(stats.indexUp).toBe(1)
  })

  it('formats change_pct as a decimal ratio without multiplying it twice', () => {
    const text = formatGlobalMarketChange(0.0366)

    expect(text).toContain('3.66%')
    expect(text).not.toContain('366%')
  })
})
