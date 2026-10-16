import { describe, expect, it } from 'vitest'
import { calculateDonchianChannels } from './donchian'

describe('Donchian20 channel', () => {
  it('leaves the first 19 samples in warm-up and uses rolling window extrema', () => {
    const candles = Array.from({ length: 21 }, (_, index) => ({
      high: index === 0 ? 99 : index === 19 ? 30 : 10 + index,
      low: index === 0 ? 1 : index === 20 ? 2 : 4 + index,
    }))
    const result = calculateDonchianChannels(candles)

    expect(result.upper.slice(0, 19)).toEqual(Array(19).fill(null))
    expect(result.lower.slice(0, 19)).toEqual(Array(19).fill(null))
    expect(result.upper[19]).toBe(99)
    expect(result.lower[19]).toBe(1)
    // At index 20, index 0 expires; the new 20-bar window is [1, 20].
    expect(result.upper[20]).toBe(30)
    expect(result.lower[20]).toBe(2)
    expect(result.middle[20]).toBe(16)
  })

  it('never uses a future candle and does not mutate input', () => {
    const candles = Object.freeze(Array.from({ length: 24 }, (_, index) => Object.freeze({
      high: 20 + index,
      low: 10 + index,
    })))
    const before = candles.map(candle => ({ ...candle }))
    const baseline = calculateDonchianChannels(candles)
    const withFutureExtreme = calculateDonchianChannels([
      ...candles.slice(0, 21), { high: 1_000_000, low: 0 }, ...candles.slice(22),
    ])

    expect(withFutureExtreme.upper.slice(0, 21)).toEqual(baseline.upper.slice(0, 21))
    expect(withFutureExtreme.lower.slice(0, 21)).toEqual(baseline.lower.slice(0, 21))
    expect(candles).toEqual(before)
  })

  it('returns null during windows containing invalid OHLC and handles bad periods safely', () => {
    const result = calculateDonchianChannels([
      { high: 5, low: 2 }, { high: Number.NaN, low: 1 }, { high: 7, low: 3 }, { high: 8, low: 4 },
    ], 2)
    expect(result.upper).toEqual([null, null, null, 8])
    expect(result.lower).toEqual([null, null, null, 3])
    expect(calculateDonchianChannels([{ high: 2, low: 1 }], 0).upper).toEqual([null])
  })
})
