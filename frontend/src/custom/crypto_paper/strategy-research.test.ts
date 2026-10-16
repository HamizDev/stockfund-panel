import { expect, it } from 'vitest'
import { STRATEGY_FIELDS, STRATEGY_NAMES, describeStrategyParameters, readStrategyParameters, validDraftParameters } from './strategy-research'

it.each(Object.entries(STRATEGY_FIELDS))('accepts the declared defaults for %s', (id, fields) => {
  const values = Object.fromEntries(fields.map(field => [field.key, String(field.defaultValue)]))
  const result = readStrategyParameters(id as keyof typeof STRATEGY_FIELDS, values)
  expect(validDraftParameters(id, result)).toBe(true)
})

it.each([
  ['dmi_confirm', { ma_period: 55, adx_threshold: true }],
  ['dmi_confirm', { ma_period: 55, adx_threshold: 51 }],
  ['squeeze_release', { lookback: 61 }],
  ['squeeze_release', { lookback: 20, future: 1 }],
  ['ema_trend', { fast_period: 40, slow_period: 20 }],
  ['__proto__', { lookback: 20 }],
])('rejects invalid saved draft %s', (id, values) => {
  expect(validDraftParameters(id, values)).toBe(false)
})

it('labels new parameters without pretending they are channel settings', () => {
  expect(describeStrategyParameters('dmi_confirm', { ma_period: 55, adx_threshold: 23 })).toContain('DI连续3根')
  expect(describeStrategyParameters('squeeze_release', { lookback: 30 })).toBe('收缩回看 30 根 K 线')
})

it.each([
  ['supertrend_pullback', { atr_period: 10, atr_multiplier: 3, ema_period: 20 }],
  ['bb_aroon', { bb_period: 20, aroon_period: 100 }],
])('accepts and summarizes the default %s parameters', (id, values) => {
  expect(readStrategyParameters(id as keyof typeof STRATEGY_FIELDS, values)).toEqual(values)
  expect(validDraftParameters(id, values)).toBe(true)
  expect(describeStrategyParameters(id as keyof typeof STRATEGY_FIELDS, values)).not.toContain('通道回看')
})

it('describes the fixed 2σ BB/Aroon rule and accepts its parameter endpoints', () => {
  expect(describeStrategyParameters('bb_aroon')).toBe('布林带 20 · 固定 2σ · Aroon 100（90/70）')
  expect(readStrategyParameters('supertrend_pullback', { atr_period: 5, atr_multiplier: 1, ema_period: 10 }))
    .toEqual({ atr_period: 5, atr_multiplier: 1, ema_period: 10 })
  expect(readStrategyParameters('supertrend_pullback', { atr_period: 30, atr_multiplier: 5, ema_period: 120 }))
    .toEqual({ atr_period: 30, atr_multiplier: 5, ema_period: 120 })
  expect(readStrategyParameters('bb_aroon', { bb_period: 10, aroon_period: 20 }))
    .toEqual({ bb_period: 10, aroon_period: 20 })
  expect(readStrategyParameters('bb_aroon', { bb_period: 60, aroon_period: 120 }))
    .toEqual({ bb_period: 60, aroon_period: 120 })
})

it.each([
  ['supertrend_pullback', { atr_period: 4, atr_multiplier: 3, ema_period: 20 }],
  ['supertrend_pullback', { atr_period: 10, atr_multiplier: 6, ema_period: 20 }],
  ['supertrend_pullback', { atr_period: 10.5, atr_multiplier: 3, ema_period: 20 }],
  ['supertrend_pullback', { atr_period: 10, atr_multiplier: 3, ema_period: 121 }],
  ['bb_aroon', { bb_period: 9, aroon_period: 100 }],
  ['bb_aroon', { bb_period: 20, aroon_period: 121 }],
  ['bb_aroon', { bb_period: 20, aroon_period: 100.5 }],
])('rejects out-of-range or noninteger parameters for %s', (id, values) => {
  expect(() => readStrategyParameters(id as keyof typeof STRATEGY_FIELDS, values)).toThrow()
  expect(validDraftParameters(id, values)).toBe(false)
})

it('keeps a complete Chinese display name for every strategy id', () => {
  expect(STRATEGY_NAMES).toEqual({
    ema_trend: 'EMA 趋势',
    channel_breakout: '通道突破',
    dmi_confirm: 'DMI 确认',
    squeeze_release: 'TTM 收缩释放',
    supertrend_pullback: 'Supertrend 回踩',
    bb_aroon: '布林带 Aroon',
  })
})
