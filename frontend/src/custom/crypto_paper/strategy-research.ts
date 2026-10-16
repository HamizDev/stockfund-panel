import type { CryptoStrategyId } from './client'

export const STRATEGY_NAMES: Record<CryptoStrategyId, string> = {
  ema_trend: 'EMA 趋势',
  channel_breakout: '通道突破',
  dmi_confirm: 'DMI 确认',
  squeeze_release: 'TTM 收缩释放',
  supertrend_pullback: 'Supertrend 回踩',
  bb_aroon: '布林带 Aroon',
}

export const STRATEGY_FIELDS: Record<CryptoStrategyId, { key: string; label: string; min: number; max: number; defaultValue: number }[]> = {
  ema_trend: [
    { key: 'fast_period', label: 'EMA 快线周期', min: 5, max: 50, defaultValue: 20 },
    { key: 'slow_period', label: 'EMA 慢线周期', min: 20, max: 120, defaultValue: 60 },
  ],
  channel_breakout: [{ key: 'lookback', label: '通道回看周期', min: 5, max: 120, defaultValue: 20 }],
  dmi_confirm: [
    { key: 'ma_period', label: 'DMI 过滤均线周期', min: 20, max: 120, defaultValue: 55 },
    { key: 'adx_threshold', label: 'ADX 强度门槛', min: 10, max: 50, defaultValue: 23 },
  ],
  squeeze_release: [{ key: 'lookback', label: '收缩回看周期', min: 10, max: 60, defaultValue: 20 }],
  supertrend_pullback: [
    { key: 'atr_period', label: 'Supertrend ATR 周期', min: 5, max: 30, defaultValue: 10 },
    { key: 'atr_multiplier', label: 'Supertrend ATR 倍数', min: 1, max: 5, defaultValue: 3 },
    { key: 'ema_period', label: '趋势 EMA 周期', min: 10, max: 120, defaultValue: 20 },
  ],
  bb_aroon: [
    { key: 'bb_period', label: '布林带周期', min: 10, max: 60, defaultValue: 20 },
    { key: 'aroon_period', label: 'Aroon 周期', min: 20, max: 120, defaultValue: 100 },
  ],
}

export function readStrategyParameters(id: CryptoStrategyId, values: Record<string, unknown>): Record<string, number> {
  const result: Record<string, number> = {}
  for (const field of STRATEGY_FIELDS[id]) {
    const original = values[field.key]
    const value = typeof original === 'number' || (typeof original === 'string' && original.trim()) ? Number(original) : NaN
    if (!Number.isInteger(value) || value < field.min || value > field.max) {
      throw new Error(`${field.label}须为 ${field.min} 至 ${field.max} 的整数。`)
    }
    result[field.key] = value
  }
  if (id === 'ema_trend' && result.fast_period >= result.slow_period) throw new Error('EMA 快线周期必须小于慢线周期。')
  return result
}

export function validDraftParameters(id: unknown, values: unknown): boolean {
  if (typeof id !== 'string' || !Object.hasOwn(STRATEGY_FIELDS, id) || !values || typeof values !== 'object' || Array.isArray(values)) return false
  const fields = STRATEGY_FIELDS[id as CryptoStrategyId]
  const params = values as Record<string, unknown>
  if (Object.keys(params).length !== fields.length || fields.some(field => typeof params[field.key] !== 'number')) return false
  try { readStrategyParameters(id as CryptoStrategyId, params); return true } catch { return false }
}

export function describeStrategyParameters(id: CryptoStrategyId, values: Record<string, number> = {}): string {
  const read = (key: string) => values[key] ?? STRATEGY_FIELDS[id]?.find(field => field.key === key)?.defaultValue
  if (id === 'ema_trend') return `EMA ${read('fast_period')} / ${read('slow_period')}`
  if (id === 'dmi_confirm') return `EMA ${read('ma_period')} · ADX>${read('adx_threshold')} · DI连续3根`
  if (id === 'supertrend_pullback') return `Supertrend ATR ${read('atr_period')} · ${read('atr_multiplier')} 倍 · EMA ${read('ema_period')}`
  if (id === 'bb_aroon') return `布林带 ${read('bb_period')} · 固定 2σ · Aroon ${read('aroon_period')}（90/70）`
  return `${id === 'squeeze_release' ? '收缩' : '通道'}回看 ${read('lookback')} 根 K 线`
}
