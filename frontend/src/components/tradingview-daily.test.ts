import { expect, it } from 'vitest'
import {
  dailyChartDateFromTime,
  normalizeTradingViewDailyRows,
  resolveDailyViewport,
  toTradingViewCandles,
  toTradingViewMarkers,
  toTradingViewMovingAverage,
  toTradingViewVolume,
} from './tradingview-daily'

const row = (date: string, patch: Record<string, unknown> = {}) => ({
  date,
  open: 10,
  high: 12,
  low: 9,
  close: 11,
  volume: 100,
  ma5: 10.5,
  ma10: null,
  ma20: null,
  ma60: null,
  ...patch,
})

it('keeps a valid price candle when volume is missing or invalid and omits only that volume bar', () => {
  const result = normalizeTradingViewDailyRows([
    row('2026-09-28', { volume: null }),
    row('2026-09-29', { volume: -1 }),
    row('2026-09-30', { volume: 0 }),
  ])

  expect(result.bars).toHaveLength(3)
  expect(result.missingVolumeBars).toBe(2)
  expect(toTradingViewCandles(result.bars)).toHaveLength(3)
  expect(toTradingViewVolume(result.bars)).toEqual([
    { time: '2026-09-30', value: 0, color: 'rgba(199,64,64,0.48)' },
  ])
  expect(toTradingViewMovingAverage(result.bars, 'ma5')).toEqual([
    { time: '2026-09-28', value: 10.5 },
    { time: '2026-09-29', value: 10.5 },
    { time: '2026-09-30', value: 10.5 },
  ])
})

it('drops invalid dates and impossible OHLC geometry without coercing string prices', () => {
  const result = normalizeTradingViewDailyRows([
    row('2026-02-30'),
    row('2026-09-28', { high: 10.5 }),
    row('2026-09-29', { close: '11' }),
    row('2026-09-30'),
  ])

  expect(result.invalidRows).toBe(3)
  expect(result.bars.map(bar => bar.date)).toEqual(['2026-09-30'])
})

it('deduplicates identical daily rows and rejects a date with conflicting OHLC or volume', () => {
  const result = normalizeTradingViewDailyRows([
    row('2026-09-28'),
    row('2026-09-28'),
    row('2026-09-29'),
    row('2026-09-29', { close: 11.5, high: 12 }),
    row('2026-09-30'),
    row('2026-09-30', { volume: 101 }),
  ])

  expect(result.bars.map(bar => bar.date)).toEqual(['2026-09-28'])
  expect(result.conflictingDates).toBe(2)
})

it('accepts only exact dates for markers and never moves a missing-date marker to a neighbor bar', () => {
  const markers = toTradingViewMarkers([
    { date: '2026-09-28', kind: 'buy', label: '买' },
    { date: '2026-09-29', kind: 'sell', label: '卖' },
    { date: '2026-10-01', kind: 'neutral', label: '缺日' },
    { date: '2026-02-30', kind: 'buy' },
  ], new Set(['2026-09-28', '2026-09-30']))

  expect(markers).toEqual([
    {
      time: '2026-09-28',
      position: 'belowBar',
      shape: 'arrowUp',
      color: '#C74040',
      text: '买',
      size: 1,
    },
  ])
  expect(dailyChartDateFromTime({ year: 2026, month: 9, day: 30 })).toBe('2026-09-30')
  expect(dailyChartDateFromTime({ year: 2026, month: 2, day: 30 })).toBeNull()
})

it('resets to the requested initial window on context changes and preserves user zoom on refresh', () => {
  expect(resolveDailyViewport({
    previousRange: { from: 20, to: 39 },
    previousCount: 40,
    nextCount: 60,
    contextChanged: true,
    visibleBars: 12,
  })).toEqual({ kind: 'range', range: { from: 48, to: 59 } })

  expect(resolveDailyViewport({
    previousRange: { from: 10, to: 19 },
    previousCount: 40,
    nextCount: 41,
    contextChanged: false,
    visibleBars: 12,
  })).toEqual({ kind: 'range', range: { from: 10, to: 19 } })

  expect(resolveDailyViewport({
    previousRange: { from: 20, to: 39 },
    previousCount: 40,
    nextCount: 41,
    contextChanged: false,
    visibleBars: 12,
  })).toEqual({ kind: 'range', range: { from: 21, to: 40 } })
})
