import type { GlobalMarket, GlobalMarketChartInterval, GlobalMarketCurrency, GlobalMarketTimeZone } from './types'

export const GLOBAL_MARKET_TIMEZONES: Record<GlobalMarket, GlobalMarketTimeZone> = {
  hk: 'Asia/Hong_Kong',
  us: 'America/New_York',
}

export const GLOBAL_MARKET_CURRENCIES: Record<GlobalMarket, GlobalMarketCurrency> = {
  hk: 'HKD',
  us: 'USD',
}

export const GLOBAL_MARKET_DEFAULTS: Record<GlobalMarket, { symbol: string; interval: GlobalMarketChartInterval }> = {
  hk: { symbol: '00700.HK', interval: '1d' },
  us: { symbol: 'SPY', interval: '1d' },
}

export function formatGlobalMarketPrice(value: number | null | undefined, currency: GlobalMarketCurrency): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency, maximumFractionDigits: 4,
  }).format(value)
}

export function formatGlobalInstrumentPrice(value: number | null | undefined, currency: GlobalMarketCurrency, kind?: string): string {
  if (kind !== 'index') return formatGlobalMarketPrice(value, currency)
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 点`
    : '—'
}

export function formatGlobalMarketChange(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('zh-CN', {
    style: 'percent', signDisplay: 'always', maximumFractionDigits: 2,
  }).format(value)
}

export function formatGlobalMarketTime(
  time: string | number,
  timezone: GlobalMarketTimeZone,
  includeYear = false,
): string {
  if (typeof time === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(time)) return time
  const date = new Date(typeof time === 'number' ? time * 1_000 : time)
  if (!Number.isFinite(date.getTime())) return String(time)
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: timezone,
    year: includeYear ? 'numeric' : undefined,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date)
}

export function globalMarketStorageKey(market: GlobalMarket): 'globalMarketsHk' | 'globalMarketsUs' {
  return market === 'hk' ? 'globalMarketsHk' : 'globalMarketsUs'
}

export function globalMarketIntervalLabel(interval: GlobalMarketChartInterval): string {
  if (interval === '1d') return '日K'
  return interval
}

export function globalMarketKindLabel(kind: string): string {
  if (kind === 'stock') return '股票'
  if (kind === 'etf') return 'ETF'
  if (kind === 'index') return '指数'
  return '其他'
}

export function globalMarketAccessLabel(access: string): string {
  if (access === 'free_api') return '免费 API'
  if (access === 'free_web') return '免费网页'
  if (access === 'approval_required') return '需申请'
  if (access === 'unavailable') return '暂不可用'
  return access
}
