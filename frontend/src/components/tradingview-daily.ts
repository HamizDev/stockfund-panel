import type {
  CandlestickData,
  HistogramData,
  LineData,
  SeriesMarker,
  Time,
} from 'lightweight-charts'
import type { ChartMarker } from '@/components/EChartsCandlestick'

export const MOVING_AVERAGE_KEYS = ['ma5', 'ma10', 'ma20', 'ma60'] as const
export type MovingAverageKey = typeof MOVING_AVERAGE_KEYS[number]

export interface DailyKlineInput {
  date?: unknown
  open?: unknown
  high?: unknown
  low?: unknown
  close?: unknown
  volume?: unknown
  ma5?: unknown
  ma10?: unknown
  ma20?: unknown
  ma60?: unknown
}

export interface TradingViewDailyBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number | null
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60: number | null
}

export interface TradingViewDailyValidation {
  bars: TradingViewDailyBar[]
  invalidRows: number
  conflictingDates: number
  missingVolumeBars: number
}

export interface LogicalViewport {
  from: number
  to: number
}

export type ViewportDecision =
  | { kind: 'fit' }
  | { kind: 'range'; range: LogicalViewport }
  | { kind: 'none' }

const DEFAULT_BUY = '#C74040'
const DEFAULT_SELL = '#2D9B65'
const DEFAULT_NEUTRAL = '#A1A1AA'

export function isDailyChartDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day))
  return date.getUTCFullYear() === year
    && date.getUTCMonth() === month - 1
    && date.getUTCDate() === day
}

function finitePositive(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

function finiteNonnegative(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function optionalAverage(value: unknown): number | null {
  return finitePositive(value) ? value : null
}

function sameCoreBar(a: TradingViewDailyBar, b: TradingViewDailyBar): boolean {
  return a.open === b.open
    && a.high === b.high
    && a.low === b.low
    && a.close === b.close
}

function mergeDuplicateBar(
  current: TradingViewDailyBar,
  incoming: TradingViewDailyBar,
): TradingViewDailyBar | null {
  if (!sameCoreBar(current, incoming)) return null
  if (current.volume != null && incoming.volume != null && current.volume !== incoming.volume) return null

  const next = { ...current }
  if (next.volume == null) next.volume = incoming.volume
  for (const key of MOVING_AVERAGE_KEYS) {
    const oldValue = next[key]
    const newValue = incoming[key]
    if (oldValue != null && newValue != null && oldValue !== newValue) {
      next[key] = null
    } else if (oldValue == null) {
      next[key] = newValue
    }
  }
  return next
}

export function normalizeTradingViewDailyRows(
  input: readonly DailyKlineInput[],
): TradingViewDailyValidation {
  const byDate = new Map<string, TradingViewDailyBar>()
  const conflictingDates = new Set<string>()
  let invalidRows = 0

  for (const row of input) {
    if (!row || !isDailyChartDate(row.date)
      || !finitePositive(row.open)
      || !finitePositive(row.high)
      || !finitePositive(row.low)
      || !finitePositive(row.close)
      || row.high < Math.max(row.open, row.close, row.low)
      || row.low > Math.min(row.open, row.close, row.high)) {
      invalidRows += 1
      continue
    }

    const next: TradingViewDailyBar = {
      date: row.date,
      open: row.open,
      high: row.high,
      low: row.low,
      close: row.close,
      volume: finiteNonnegative(row.volume) ? row.volume : null,
      ma5: optionalAverage(row.ma5),
      ma10: optionalAverage(row.ma10),
      ma20: optionalAverage(row.ma20),
      ma60: optionalAverage(row.ma60),
    }
    const previous = byDate.get(next.date)
    if (!previous) {
      byDate.set(next.date, next)
      continue
    }

    const merged = mergeDuplicateBar(previous, next)
    if (!merged) {
      conflictingDates.add(next.date)
      byDate.delete(next.date)
    } else if (!conflictingDates.has(next.date)) {
      byDate.set(next.date, merged)
    }
  }

  const bars = [...byDate.values()]
    .filter(bar => !conflictingDates.has(bar.date))
    .sort((a, b) => a.date.localeCompare(b.date))

  return {
    bars,
    invalidRows,
    conflictingDates: conflictingDates.size,
    missingVolumeBars: bars.filter(bar => bar.volume == null).length,
  }
}

export function toTradingViewCandles(bars: readonly TradingViewDailyBar[]): CandlestickData<Time>[] {
  return bars.map(bar => ({
    time: bar.date,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
  }))
}

export function toTradingViewVolume(bars: readonly TradingViewDailyBar[]): HistogramData<Time>[] {
  return bars.flatMap(bar => bar.volume == null ? [] : [{
    time: bar.date,
    value: bar.volume,
    color: bar.close >= bar.open ? 'rgba(199,64,64,0.48)' : 'rgba(45,155,101,0.48)',
  }])
}

export function toTradingViewMovingAverage(
  bars: readonly TradingViewDailyBar[],
  key: MovingAverageKey,
): LineData<Time>[] {
  return bars.flatMap(bar => {
    const value = bar[key]
    return value == null ? [] : [{ time: bar.date, value }]
  })
}

export function dailyChartDateFromTime(time: unknown): string | null {
  if (isDailyChartDate(time)) return time
  if (time && typeof time === 'object') {
    const businessDay = time as { year?: unknown; month?: unknown; day?: unknown }
    const { year, month, day } = businessDay
    if (Number.isInteger(year) && Number.isInteger(month) && Number.isInteger(day)
      && Number(year) >= 1 && Number(month) >= 1 && Number(month) <= 12
      && Number(day) >= 1 && Number(day) <= 31) {
      const date = [
        String(year).padStart(4, '0'),
        String(month).padStart(2, '0'),
        String(day).padStart(2, '0'),
      ].join('-')
      return isDailyChartDate(date) ? date : null
    }
  }
  if (typeof time === 'number' && Number.isFinite(time)) {
    const date = new Date(time * 1000)
    const value = [
      String(date.getUTCFullYear()).padStart(4, '0'),
      String(date.getUTCMonth() + 1).padStart(2, '0'),
      String(date.getUTCDate()).padStart(2, '0'),
    ].join('-')
    return isDailyChartDate(value) ? value : null
  }
  return null
}

export function toTradingViewMarkers(
  markers: readonly ChartMarker[] | undefined,
  availableDates: ReadonlySet<string>,
): SeriesMarker<Time>[] {
  if (!markers?.length) return []
  return markers
    .filter(marker => isDailyChartDate(marker.date) && availableDates.has(marker.date))
    .map((marker): SeriesMarker<Time> => ({
      time: marker.date,
      position: marker.above || marker.kind === 'sell' ? 'aboveBar' : 'belowBar',
      shape: marker.above
        ? 'circle'
        : marker.kind === 'buy'
          ? 'arrowUp'
          : marker.kind === 'sell'
            ? 'arrowDown'
            : 'circle',
      color: marker.color?.trim() ? marker.color : (
        marker.kind === 'buy' ? DEFAULT_BUY
          : marker.kind === 'sell' ? DEFAULT_SELL
            : DEFAULT_NEUTRAL
      ),
      text: marker.label ?? '',
      size: marker.label ? 1 : 0.8,
    }))
    .sort((a, b) => String(a.time).localeCompare(String(b.time)))
}

export function resolveDailyViewport(input: {
  previousRange: LogicalViewport | null
  previousCount: number
  nextCount: number
  contextChanged: boolean
  visibleBars: number | 'all'
}): ViewportDecision {
  const { previousRange, previousCount, nextCount, contextChanged, visibleBars } = input
  if (nextCount <= 0) return { kind: 'none' }

  if (contextChanged || !previousRange || previousCount <= 0) {
    if (visibleBars === 'all' || nextCount === 1) return { kind: 'fit' }
    const count = typeof visibleBars === 'number' && Number.isFinite(visibleBars)
      ? Math.max(1, Math.floor(visibleBars))
      : 60
    return {
      kind: 'range',
      range: { from: Math.max(0, nextCount - count), to: nextCount - 1 },
    }
  }

  const width = Math.max(1, previousRange.to - previousRange.from)
  const followedLatestBar = previousRange.to >= previousCount - 1
  if (followedLatestBar) {
    const to = nextCount - 1
    return { kind: 'range', range: { from: Math.max(0, to - width), to } }
  }

  if (previousRange.to >= 0 && previousRange.from <= nextCount - 1) {
    return { kind: 'range', range: previousRange }
  }
  return { kind: 'fit' }
}

export function formatTradingViewPriceSummary(bar: TradingViewDailyBar): string {
  const volume = bar.volume == null ? '成交量缺失' : '成交量 ' + bar.volume.toLocaleString('zh-CN')
  return bar.date
    + '，开 ' + bar.open.toFixed(2)
    + '，高 ' + bar.high.toFixed(2)
    + '，低 ' + bar.low.toFixed(2)
    + '，收 ' + bar.close.toFixed(2)
    + '，' + volume
}
