import { describe, expect, it } from 'vitest'
import { formatGlobalMarketChange, formatGlobalMarketPrice, formatGlobalMarketTime } from './marketFormatters'

describe('global market formatters', () => {
  it('keeps local daily dates and formats minute timestamps in each market timezone', () => {
    const minute = Date.parse('2026-07-10T14:00:00Z') / 1_000

    expect(formatGlobalMarketTime('2026-07-10', 'Asia/Hong_Kong')).toBe('2026-07-10')
    expect(formatGlobalMarketTime(minute, 'Asia/Hong_Kong', true)).toContain('22:00')
    expect(formatGlobalMarketTime(minute, 'America/New_York', true)).toContain('10:00')
    expect(formatGlobalMarketTime(minute, 'Asia/Hong_Kong', true))
      .not.toBe(formatGlobalMarketTime(minute, 'America/New_York', true))
  })

  it('formats HKD and USD separately and treats change_pct as a decimal ratio', () => {
    expect(formatGlobalMarketPrice(700, 'HKD')).toContain('HK$')
    expect(formatGlobalMarketPrice(525, 'USD')).toContain('$')
    expect(formatGlobalMarketChange(0.0366)).toContain('3.66%')
    expect(formatGlobalMarketPrice(null, 'HKD')).toBe('—')
  })
})
import { formatGlobalInstrumentPrice } from './marketFormatters'

it('formats explicit indices as points and missing prices as unknown', () => {
  expect(formatGlobalInstrumentPrice(1234.56, 'USD', 'index')).toBe('1,234.56 点')
  expect(formatGlobalInstrumentPrice(1234.56, 'HKD', 'index')).not.toContain('HK$')
  expect(formatGlobalInstrumentPrice(null, 'USD', 'index')).toBe('—')
})
