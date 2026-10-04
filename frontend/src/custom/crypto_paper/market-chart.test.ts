// @vitest-environment node
import { describe, expect, it } from 'vitest'
import type { UTCTimestamp } from 'lightweight-charts'
import {
  buildTradeMarkers,
  calculateEma,
  formatBeijingTime,
  normalizeMarketBars,
  type CryptoMarketBar,
  type CryptoMarketTrade,
} from './market-chart'

function bar(openTime: number, values: Partial<Pick<CryptoMarketBar, 'open' | 'high' | 'low' | 'close' | 'volume'>> = {}): CryptoMarketBar {
  return {
    open_time_ms: openTime,
    close_time_ms: openTime + 59_999,
    open: values.open ?? '100',
    high: values.high ?? '110',
    low: values.low ?? '90',
    close: values.close ?? '105',
    volume: values.volume ?? '12.5',
  }
}

describe('normalizeMarketBars', () => {
  it('sorts candles, converts milliseconds to UTC seconds once, and retains zero volume', () => {
    const bars = normalizeMarketBars([bar(60_000, { volume: '0' }), bar(0)])

    expect(bars.map(item => item.openTimeMs)).toEqual([0, 60_000])
    expect(bars.map(item => item.time)).toEqual([0, 60])
    expect(bars[1].volume).toBe(0)
  })

  it('discards invalid numeric values and impossible OHLC ranges instead of coercing them to zero', () => {
    const bars = normalizeMarketBars([
      bar(0, { close: 'NaN' }),
      bar(60_000, { volume: 'Infinity' }),
      bar(120_000, { high: '99' }),
      bar(180_000, { low: '106' }),
      bar(240_000, { open: '' }),
      { ...bar(270_000), close_time_ms: 270_000 },
      bar(300_000),
    ])

    expect(bars.map(item => item.openTimeMs)).toEqual([300_000])
  })

  it('rejects every candle when an open timestamp is duplicated', () => {
    const bars = normalizeMarketBars([bar(0), bar(0, { close: '108' }), bar(60_000)])

    expect(bars.map(item => item.openTimeMs)).toEqual([60_000])
  })

  it('rejects subsecond timestamps that collide after Lightweight Charts second conversion', () => {
    expect(normalizeMarketBars([bar(1_000), bar(1_500)])).toEqual([])
  })
})

describe('calculateEma', () => {
  it('uses the first close as seed and applies the standard 2/(period + 1) recurrence', () => {
    const bars = normalizeMarketBars([
      bar(0, { open: '100', high: '100', low: '100', close: '100' }),
      bar(60_000, { open: '110', high: '110', low: '110', close: '110' }),
      bar(120_000, { open: '120', high: '120', low: '120', close: '120' }),
    ])

    expect(calculateEma(bars, 3)).toEqual([
      { time: 0, value: 100 },
      { time: 60, value: 105 },
      { time: 120, value: 112.5 },
    ])
    expect(calculateEma(bars, 0)).toEqual([])
  })
})

describe('buildTradeMarkers', () => {
  const bars = normalizeMarketBars([bar(0), bar(60_000), bar(120_000)])

  it('maps confirmed actions to the existing candle containing the event', () => {
    const trades: CryptoMarketTrade[] = [
      { id: 'long', at_ms: 61_234, market: 'usdm', symbol: 'BTCUSDT', action: 'open_long', kind: 'trade' },
      { id: 'short-close', at: '1970-01-01T00:02:40.000Z', market: 'usdm', symbol: 'BTCUSDT', action: 'close_short' },
    ]

    expect(buildTradeMarkers(trades, bars, '1m', 'BTCUSDT')).toEqual([
      { time: 60, position: 'belowBar', shape: 'arrowUp', color: '#22c55e', text: '开多' },
      { time: 120, position: 'belowBar', shape: 'arrowUp', color: '#22c55e', text: '平空' },
    ])
  })

  it('filters wrong market/symbol, funding, liquidation, missing times, and events without an existing candle', () => {
    const trades: CryptoMarketTrade[] = [
      { id: 'spot', at_ms: 1_000, market: 'spot', symbol: 'BTCUSDT', action: 'buy' },
      { id: 'other-symbol', at_ms: 1_000, market: 'usdm', symbol: 'ETHUSDT', action: 'buy' },
      { id: 'wrong-usdm-action', at_ms: 1_000, market: 'usdm', symbol: 'BTCUSDT', action: 'buy' },
      { id: 'funding', at_ms: 1_000, market: 'usdm', symbol: 'BTCUSDT', action: 'funding', kind: 'funding' },
      { id: 'liquidation', at_ms: 1_000, market: 'usdm', symbol: 'BTCUSDT', action: 'liquidation', kind: 'liquidation' },
      { id: 'invalid-time', at_ms: Number.NaN, at: '1970-01-01T00:00:01Z', market: 'usdm', symbol: 'BTCUSDT', action: 'buy' },
      { id: 'future', at_ms: 180_000, market: 'usdm', symbol: 'BTCUSDT', action: 'sell' },
    ]

    expect(buildTradeMarkers(trades, bars, '1m', 'BTCUSDT')).toEqual([])
  })

  it('uses timeframe buckets and defaults the market to USDM', () => {
    const hourlyBars = normalizeMarketBars([bar(3_600_000)])
    const trades: CryptoMarketTrade[] = [
      { id: 'within-hour', at_ms: 7_199_999, market: 'usdm', symbol: 'BTCUSDT', action: 'open_short' },
    ]

    expect(buildTradeMarkers(trades, hourlyBars, '1h', 'BTCUSDT')).toEqual([
      { time: 3_600, position: 'aboveBar', shape: 'arrowDown', color: '#ef4444', text: '开空' },
    ])
  })
})

it('formats Unix seconds with an explicit Beijing timezone', () => {
  expect(formatBeijingTime(0 as UTCTimestamp, true)).toBe('1970-01-01 08:00')
  expect(formatBeijingTime(57_600 as UTCTimestamp, true)).toBe('1970-01-02 00:00')
})
