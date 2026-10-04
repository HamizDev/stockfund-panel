import type { Time } from 'lightweight-charts'

export type CryptoChartInterval = '1m' | '5m' | '15m' | '1h' | '4h'
export type CryptoChartMarket = 'spot' | 'usdm'

export interface CryptoMarketBar {
  open_time_ms: number
  close_time_ms: number
  open: string
  high: string
  low: string
  close: string
  volume: string
}

export interface NormalizedMarketBar {
  openTimeMs: number
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface CryptoMarketTrade {
  id: string
  at?: string
  at_ms?: number
  market?: string
  symbol?: string
  action?: string
  kind?: string
}

export interface MarketTradeMarker {
  time: number
  position: 'aboveBar' | 'belowBar'
  shape: 'arrowUp' | 'arrowDown'
  color: string
  text: string
}

const INTERVAL_MS: Record<CryptoChartInterval, number> = {
  '1m': 60_000,
  '5m': 5 * 60_000,
  '15m': 15 * 60_000,
  '1h': 60 * 60_000,
  '4h': 4 * 60 * 60_000,
}

const UP_COLOR = '#22c55e'
const DOWN_COLOR = '#ef4444'

const TRADE_MARKERS: Record<string, Omit<MarketTradeMarker, 'time'>> = {
  buy: { position: 'belowBar', shape: 'arrowUp', color: UP_COLOR, text: '买' },
  open_long: { position: 'belowBar', shape: 'arrowUp', color: UP_COLOR, text: '开多' },
  close_short: { position: 'belowBar', shape: 'arrowUp', color: UP_COLOR, text: '平空' },
  sell: { position: 'aboveBar', shape: 'arrowDown', color: DOWN_COLOR, text: '卖' },
  open_short: { position: 'aboveBar', shape: 'arrowDown', color: DOWN_COLOR, text: '开空' },
  close_long: { position: 'aboveBar', shape: 'arrowDown', color: DOWN_COLOR, text: '平多' },
}

const SPOT_ACTIONS = new Set(['buy', 'sell'])
const USDM_ACTIONS = new Set(['open_long', 'open_short', 'close_long', 'close_short'])

function parsePositiveNumber(value: string): number | null {
  if (typeof value !== 'string' || value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

function parseVolume(value: string): number | null {
  if (typeof value !== 'string' || value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null
}

/**
 * Validate and sort API candles before sending them to Lightweight Charts.
 * If an open time appears more than once, every copy is discarded so the chart
 * never picks an arbitrary candle from an ambiguous response.
 */
export function normalizeMarketBars(bars: readonly CryptoMarketBar[]): NormalizedMarketBar[] {
  const parsed: Array<NormalizedMarketBar | null> = []
  const counts = new Map<number, number>()
  const secondCounts = new Map<number, number>()

  for (const bar of bars) {
    const openTimeMs = bar?.open_time_ms
    if (!Number.isSafeInteger(openTimeMs) || openTimeMs < 0) {
      parsed.push(null)
      continue
    }
    counts.set(openTimeMs, (counts.get(openTimeMs) ?? 0) + 1)
    const time = Math.floor(openTimeMs / 1_000)
    secondCounts.set(time, (secondCounts.get(time) ?? 0) + 1)
    if (!Number.isSafeInteger(bar.close_time_ms) || bar.close_time_ms <= openTimeMs) {
      parsed.push(null)
      continue
    }

    const open = parsePositiveNumber(bar.open)
    const high = parsePositiveNumber(bar.high)
    const low = parsePositiveNumber(bar.low)
    const close = parsePositiveNumber(bar.close)
    const volume = parseVolume(bar.volume)
    if (open === null || high === null || low === null || close === null || volume === null) {
      parsed.push(null)
      continue
    }
    if (high < Math.max(open, close, low) || low > Math.min(open, close, high)) {
      parsed.push(null)
      continue
    }

    parsed.push({
      openTimeMs,
      // Lightweight Charts expects Unix seconds. This is the only ms-to-seconds
      // conversion; labels format the resulting UTC time in Asia/Shanghai.
      time,
      open,
      high,
      low,
      close,
      volume,
    })
  }

  return parsed
    .filter((bar): bar is NormalizedMarketBar =>
      bar !== null && counts.get(bar.openTimeMs) === 1 && secondCounts.get(bar.time) === 1)
    .sort((left, right) => left.openTimeMs - right.openTimeMs)
}

/** EMA uses the first close as its seed, then the standard 2/(period + 1) recurrence. */
export function calculateEma(
  bars: readonly NormalizedMarketBar[],
  period: number,
): Array<{ time: number; value: number }> {
  if (!Number.isInteger(period) || period < 1 || bars.length === 0) return []
  const alpha = 2 / (period + 1)
  const points: Array<{ time: number; value: number }> = []
  let previous: number | null = null

  for (const bar of bars) {
    if (!Number.isFinite(bar.close) || bar.close <= 0 || !Number.isFinite(bar.time)) continue
    const value: number = previous === null ? bar.close : alpha * bar.close + (1 - alpha) * previous
    if (!Number.isFinite(value)) continue
    points.push({ time: bar.time, value })
    previous = value
  }

  return points
}

function tradeTimeMs(trade: CryptoMarketTrade): number | null {
  if (trade.at_ms !== undefined) {
    return Number.isSafeInteger(trade.at_ms) && trade.at_ms >= 0 ? trade.at_ms : null
  }
  if (typeof trade.at !== 'string' || trade.at.trim() === '') return null
  const parsed = Date.parse(trade.at)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null
}

/**
 * Attach only confirmed spot/USDM trade actions to the exact candle interval
 * containing the recorded event. Missing candles, mismatched market/symbols,
 * funding, liquidation, and future/out-of-range events are intentionally omitted.
 */
export function buildTradeMarkers(
  trades: readonly CryptoMarketTrade[],
  bars: readonly NormalizedMarketBar[],
  interval: CryptoChartInterval,
  symbol: string,
  market: CryptoChartMarket = 'usdm',
): MarketTradeMarker[] {
  if (bars.length === 0 || symbol.trim() === '') return []
  const intervalMs = INTERVAL_MS[interval]
  const barByOpenTime = new Map(bars.map(bar => [bar.openTimeMs, bar]))
  const seenIds = new Set<string>()
  const markers: Array<MarketTradeMarker & { id: string }> = []

  for (const trade of trades) {
    if (!trade.id || seenIds.has(trade.id)) continue
    if (trade.kind !== undefined && trade.kind !== 'trade') continue
    if (trade.market !== market || trade.symbol !== symbol) continue
    if (market === 'spot' && !SPOT_ACTIONS.has(trade.action ?? '')) continue
    if (market === 'usdm' && !USDM_ACTIONS.has(trade.action ?? '')) continue

    const marker = trade.action ? TRADE_MARKERS[trade.action] : undefined
    if (!marker) continue
    const atMs = tradeTimeMs(trade)
    if (atMs === null) continue

    const bar = barByOpenTime.get(Math.floor(atMs / intervalMs) * intervalMs)
    if (!bar) continue
    seenIds.add(trade.id)
    markers.push({ time: bar.time, ...marker, id: trade.id })
  }

  return markers
    .sort((left, right) => left.time - right.time || left.id.localeCompare(right.id))
    .map(({ id: _id, ...marker }) => marker)
}

function timeToEpochSeconds(time: Time): number | null {
  if (typeof time === 'number') return Number.isFinite(time) ? time : null
  if (typeof time === 'string') {
    const timestamp = Date.parse(time.includes('T') ? time : `${time}T00:00:00Z`)
    return Number.isFinite(timestamp) ? timestamp / 1_000 : null
  }
  const date = Date.UTC(time.year, time.month - 1, time.day)
  return Number.isFinite(date) ? date / 1_000 : null
}

const beijingDateFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

export function formatBeijingTime(time: Time, includeYear = false): string {
  const seconds = timeToEpochSeconds(time)
  if (seconds === null) return ''
  const parts = beijingDateFormatter.formatToParts(new Date(seconds * 1_000))
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]))
  const date = includeYear
    ? `${values.year}-${values.month}-${values.day}`
    : `${values.month}-${values.day}`
  return `${date} ${values.hour}:${values.minute}`
}
