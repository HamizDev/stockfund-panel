import type { KronosPeriod, KronosResult } from './types'
import type { KronosAssetType } from './state'

export interface KronosChartModel {
  categories: string[]
  historyOhlc: (KronosOhlcValue | null)[]
  forecastOhlc: (KronosOhlcValue | null)[]
  forecastCloseOnly: (number | null)[]
  forecastClose: (number | null)[]
  historyCount: number
  forecastStartIndex: number | null
  forecastStartTime: string | null
  forecastEndTime: string | null
  defaultStartIndex: number
}

/** ECharts candlestick values use [open, close, low, high] ordering. */
export type KronosOhlcValue = [open: number, close: number, low: number, high: number]

/** Align real bars and forecast data to one category axis without inventing missing OHLC values. */
export function buildKronosChartModel(result: KronosResult): KronosChartModel {
  const history = result.history
  // Keep truthful close values visible for legacy close-only and partial OHLC records.
  // A close-only value never becomes a synthetic candle.
  const forecast = result.forecast
  const categories = [...history.map(row => row.time), ...forecast.map(row => row.time)]
  const historyOhlc: (KronosOhlcValue | null)[] = [
    ...history.map(row => [row.open, row.close, row.low, row.high] as KronosOhlcValue),
    ...forecast.map(() => null),
  ]
  const forecastOhlc: (KronosOhlcValue | null)[] = [
    ...history.map(() => null),
    ...forecast.map(row => {
      if (!('open' in row)) return null
      return [row.open, row.close, row.low, row.high] as KronosOhlcValue
    }),
  ]
  const forecastCloseOnly: (number | null)[] = [
    ...history.map(() => null),
    ...forecast.map(row => result.forecast_mode === 'partial_ohlc' && !('open' in row) ? row.close : null),
  ]

  const forecastClose: (number | null)[] = [
    ...history.map(() => null),
    ...forecast.map(row => row.close),
  ]

  const forecastStartIndex = forecast.length > 0 ? history.length : null
  return {
    categories,
    historyOhlc,
    forecastOhlc,
    forecastCloseOnly,
    forecastClose,
    historyCount: history.length,
    forecastStartIndex,
    forecastStartTime: forecast[0]?.time ?? null,
    forecastEndTime: forecast[forecast.length - 1]?.time ?? null,
    defaultStartIndex: Math.max(0, history.length - 60),
  }
}

function shanghaiParts(date: Date): Record<string, string> {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  return Object.fromEntries(parts.map(part => [part.type, part.value]))
}

/** Show naive exchange timestamps as Beijing wall time and normalize offset timestamps to Beijing. */
export function formatKronosTime(value: string, period: KronosPeriod): string {
  const hasOffset = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value)
  if (hasOffset) {
    const date = new Date(value)
    if (Number.isFinite(date.getTime())) {
      const part = shanghaiParts(date)
      const day = `${part.year}-${part.month}-${part.day}`
      return period === 'daily' ? day : `${day} ${part.hour}:${part.minute}:${part.second}`
    }
  }
  const normalized = value.replace('T', ' ').replace(/Z$/, '')
  return period === 'daily' ? normalized.slice(0, 10) : normalized.slice(0, 19)
}

export function formatKronosAxisTime(value: string, period: KronosPeriod): string {
  const full = formatKronosTime(value, period)
  if (period === 'daily') return full.slice(5, 10)
  return full.length >= 16 ? `${full.slice(5, 10)} ${full.slice(11, 16)}` : full
}

export function formatKronosPrice(value: number, assetType?: KronosAssetType): string {
  if (assetType === 'etf') return value.toFixed(3)
  if (assetType === 'stock') return value.toFixed(2)
  return String(value)
}
