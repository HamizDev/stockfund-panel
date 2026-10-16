import { describe, expect, it } from 'vitest'
import {
  calculateEma,
  calculateLoadedRangePerformance,
  calculateTechnicalSummary,
  type TechnicalSummaryBar,
} from './technicalSummary'

function candle(close: number, time?: string): TechnicalSummaryBar {
  return {
    ...(time === undefined ? {} : { time }),
    open: close,
    high: close + 1,
    low: Math.max(0.01, close - 1),
    close,
  }
}

describe('technical summary calculations', () => {
  it('seeds EMA with a period-sized SMA and leaves warm-up values null', () => {
    expect(calculateEma([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4])
    expect(calculateEma([1, 2], 3)).toEqual([null, null])
    expect(calculateEma([1, 2], 0)).toEqual([null, null])
  })

  it('restarts EMA warm-up after a non-finite value', () => {
    expect(calculateEma([1, 2, Number.NaN, 3, 4, 5], 3)).toEqual([null, null, null, null, null, 4])
  })

  it('reports EMA20/60 only after both series are warmed up', () => {
    const early = calculateTechnicalSummary(Array.from({ length: 59 }, (_, index) => candle(index + 1)))
    expect(early.emaTrend.status).toBe('insufficient')
    expect(early.emaTrend.ema20).not.toBeNull()
    expect(early.emaTrend.ema60).toBeNull()

    const warmed = calculateTechnicalSummary(Array.from({ length: 60 }, (_, index) => candle(index + 1)))
    expect(warmed.emaTrend.status).toBe('above')
  })

  it('uses Wilder RSI warm-up and distinguishes hot, cold, and flat readings', () => {
    const rising = calculateTechnicalSummary(Array.from({ length: 15 }, (_, index) => candle(index + 1)))
    const falling = calculateTechnicalSummary(Array.from({ length: 15 }, (_, index) => candle(15 - index)))
    const flat = calculateTechnicalSummary(Array.from({ length: 15 }, () => candle(10)))

    expect(rising.rsi14.status).toBe('hot')
    expect(rising.rsi14.value).toBe(100)
    expect(falling.rsi14.status).toBe('cold')
    expect(falling.rsi14.value).toBe(0)
    expect(flat.rsi14.status).toBe('normal')
    expect(flat.rsi14.value).toBe(50)
  })

  it('keeps MACD unavailable through its signal-line warm-up', () => {
    const beforeSignal = calculateTechnicalSummary(Array.from({ length: 33 }, () => candle(10)))
    const warmed = calculateTechnicalSummary(Array.from({ length: 34 }, () => candle(10)))

    expect(beforeSignal.macd.status).toBe('insufficient')
    expect(beforeSignal.macd.dif).not.toBeNull()
    expect(beforeSignal.macd.dea).toBeNull()
    expect(warmed.macd.status).toBe('equal')
    expect(warmed.macd.dif).toBe(0)
    expect(warmed.macd.dea).toBe(0)
    expect(warmed.macd.histogram).toBe(0)
  })

  it('checks breakouts against the prior 20 bars, excluding the current candle', () => {
    const bars = Array.from({ length: 20 }, () => ({ open: 5, high: 10, low: 1, close: 5 }))
    bars.push({ open: 11, high: 12, low: 10, close: 11 })
    const result = calculateTechnicalSummary(bars)

    expect(result.donchian20.status).toBe('breakout-up')
    expect(result.donchian20.previousUpper).toBe(10)
    expect(result.donchian20.previousLower).toBe(1)
  })

  it('reports insufficiency instead of a neutral state for short history', () => {
    const result = calculateTechnicalSummary(Array.from({ length: 20 }, () => candle(10)))
    expect(result.donchian20.status).toBe('insufficient')
    expect(result.emaTrend.status).toBe('insufficient')
    expect(result.macd.status).toBe('insufficient')
    expect(result.rsi14.status).toBe('normal')
  })

  it('sorts dated descending input chronologically but preserves order when time is absent', () => {
    const descending = [candle(12, '2024-01-03'), candle(10, '2024-01-02'), candle(8, '2024-01-01')]
    const sorted = calculateTechnicalSummary(descending)
    expect(sorted.timeRange).toEqual({ start: '2024-01-01', end: '2024-01-03', orderedByTime: true })
    expect(sorted.performance.returnPct).toBe(50)

    const noTime = calculateTechnicalSummary([candle(12), candle(10)])
    expect(noTime.timeRange).toEqual({ start: null, end: null, orderedByTime: false })
    expect(noTime.performance.returnPct).toBeCloseTo(-16.6667, 3)
  })

  it('calculates loaded-range return, extremes, and close-based drawdown', () => {
    const performance = calculateLoadedRangePerformance([
      { high: 12, low: 8, close: 10 },
      { high: 15, low: 11, close: 12 },
      { high: 10, low: 8, close: 9 },
    ])
    expect(performance).toMatchObject({
      status: 'ready', high: 15, low: 8,
      maxDrawdownPct: -25, startClose: 10, endClose: 9,
    })
    expect(performance.returnPct).toBeCloseTo(-10, 10)
  })

  it('rejects a zero return base and excludes negative or malformed OHLC bars', () => {
    expect(calculateLoadedRangePerformance([{ high: 1, low: 0, close: 0 }])).toMatchObject({
      status: 'invalid-base', returnPct: null,
    })

    const result = calculateTechnicalSummary([
      candle(10),
      { open: -2, high: -1, low: -3, close: -2 },
      { open: 10, high: 8, low: 11, close: 10 },
      candle(12),
    ])
    expect(result.validBarCount).toBe(2)
    expect(result.performance.returnPct).toBeCloseTo(20, 10)
  })

  it('keeps one valid bar useful for range performance without inventing warmed indicators', () => {
    const result = calculateTechnicalSummary([candle(10)])
    expect(result.validBarCount).toBe(1)
    expect(result.performance.status).toBe('ready')
    expect(result.performance.returnPct).toBe(0)
    expect(result.performance.maxDrawdownPct).toBe(0)
    expect(result.emaTrend.status).toBe('insufficient')
    expect(result.rsi14.status).toBe('insufficient')
    expect(result.macd.status).toBe('insufficient')
  })
})
