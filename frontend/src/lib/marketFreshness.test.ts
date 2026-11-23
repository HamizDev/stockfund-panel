import { expect, it } from 'vitest'
import { marketFreshness } from './marketFreshness'

const active = { enabled: true, running: true, is_trading_hours: true, mode: 'full_market', interval_s: 10, quote_age_ms: 1000 }

it('a just-received old source snapshot is still delayed', () => {
  expect(marketFreshness({ ...active, source_age_ms: 90_000 }, false)).toMatchObject({ label: '行情延迟', live: false })
})

it('shows the polling interval without claiming tick-by-tick real time', () => {
  const fresh = marketFreshness({ ...active, source_age_ms: 1000 }, false)
  expect(fresh).toMatchObject({ label: '轮询行情', live: true })
  expect(fresh.note).toContain('10 秒')
})

it.each([undefined, null, NaN, Infinity, -1])('cannot verify fresh quotes with source age %s', source_age_ms => {
  expect(marketFreshness({ ...active, source_age_ms }, false)).toMatchObject({ label: '来源时间待核对', live: false })
})

it('reports a stale local fetch even when the source age is unknown', () => {
  expect(marketFreshness({ ...active, quote_age_ms: 90_000 }, false)).toMatchObject({ label: '行情延迟', live: false })
})

it('historical and closed snapshots are never marked live', () => {
  expect(marketFreshness(active, true).live).toBe(false)
  expect(marketFreshness({ ...active, is_trading_hours: false }, false).live).toBe(false)
})
