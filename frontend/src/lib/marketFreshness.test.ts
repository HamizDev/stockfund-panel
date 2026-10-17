import { expect, it } from 'vitest'
import { marketFreshness } from './marketFreshness'

const active = { enabled: true, running: true, is_trading_hours: true, mode: 'full_market', interval_s: 10, quote_age_ms: 1000 }

it('a just-received old source snapshot is still delayed', () => {
  expect(marketFreshness({ ...active, source_age_ms: 90_000 }, false)).toMatchObject({ label: '行情延迟', live: false })
})

it('shows the polling interval without claiming tick-by-tick real time', () => {
  expect(marketFreshness({ ...active, source_age_ms: 1000 }, false)).toMatchObject({ label: '轮询行情', live: true })
  expect(marketFreshness(active, false).note).toContain('10 秒')
})

it('historical and closed snapshots are never marked live', () => {
  expect(marketFreshness(active, true).live).toBe(false)
  expect(marketFreshness({ ...active, is_trading_hours: false }, false).live).toBe(false)
})
