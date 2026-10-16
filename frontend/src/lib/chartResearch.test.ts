import { describe, expect, it } from 'vitest'
import { anchoredVwap, chartResearchPrompt, researchMetrics, trendDescription } from './chartResearch'
import { dailyChartRows } from './dailyChartRows'
import type { OHLC } from '@/components/EChartsCandlestick'

const bars: OHLC[] = [
  { date: '2026-10-12', open: 11, high: 12, low: 10, close: 11, volume: 100 },
  { date: '2026-10-13', open: 13, high: 14, low: 12, close: 13, volume: 300 },
  { date: '2026-10-14', open: 14, high: 15, low: 13, close: 14, volume: 200 },
]

describe('chart research data contracts', () => {
  it('computes weighted prices from the selected anchor, without earlier bars', () => {
    expect(anchoredVwap(bars, bars[0].date).values).toEqual([11, 12.5, 13])
    expect(anchoredVwap(bars, bars[1].date).values).toEqual([null, 13, 13.4])
  })
  it('does not recalculate historical values from later bars', () => {
    expect(anchoredVwap(bars.slice(0, 2), bars[0].date).values)
      .toEqual(anchoredVwap(bars, bars[0].date).values.slice(0, 2))
  })
  it('stops at missing volume rather than inventing a complete estimate', () => {
    const result = anchoredVwap([bars[0], { ...bars[1], volume: null }, bars[2]], bars[0].date)
    expect(result.values).toEqual([11, null, null])
    expect(result.error).toContain('2026-10-13')
  })
  it('zero volume adds no weight and an entirely zero-volume range is unavailable', () => {
    expect(anchoredVwap([bars[0], { ...bars[1], volume: 0 }], bars[0].date).values).toEqual([11, 11])
    expect(anchoredVwap([{ ...bars[0], volume: 0 }], bars[0].date).values).toEqual([null])
  })
  it('disabled and out-of-window anchors do not silently shift their origin', () => {
    expect(anchoredVwap(bars, '').values).toEqual([null, null, null])
    expect(anchoredVwap(bars, '2026-10-11').error).toContain('不在已加载')
  })
  it('keeps percent ratios explicit and allows %B outside the bands', () => {
    const metrics = researchMetrics({ ...bars[0], close: 16, ma20: 12, boll_upper: 14, boll_lower: 10, atr_14: 2 })
    expect(metrics.bollPosition).toBe(1.5)
    expect(metrics.bollWidth).toBeCloseTo(1 / 3)
    expect(metrics.atrRatio).toBe(.125)
  })
  it('does not turn absent or flat-band positions into zero', () => {
    expect(researchMetrics().adx).toBeNull()
    const flat = researchMetrics({ ...bars[0], ma20: 11, boll_upper: 11, boll_lower: 11 })
    expect(flat.bollWidth).toBe(0)
    expect(flat.bollPosition).toBeNull()
  })
  it('distinguishes strong downward movement from upward direction', () => {
    const metrics = researchMetrics({ ...bars[0], adx_14: 35, adx_plus_di_14: 10, adx_minus_di_14: 30 })
    expect(trendDescription(metrics)).toBe('趋势较强 · 下行动向占优')
    expect(trendDescription(researchMetrics())).toContain('不可计算')
  })
  it('carries research fields and live status through shared chart validation', () => {
    const [row] = dailyChartRows([{ ...bars[0], volume: 100, atr_14: .5, adx_14: 23, adx_plus_di_14: 20, is_live: true }])
    expect(row.atr_14).toBe(.5)
    expect(row.adx_14).toBe(23)
    expect(row.is_live).toBe(true)
  })
  it('AI context includes the observation date, nulls, units and unfinished-bar status', () => {
    const row = { ...bars[0], is_live: true }
    const prompt = chartResearchPrompt('518880.SH', row, researchMetrics(row), '', null)
    expect(prompt).toContain('2026-10-12，未收盘')
    expect(prompt).toContain('0.01表示1%')
    expect(prompt).toContain('"adx":null')
    expect(prompt).toContain('不是主力成本')
  })
})
