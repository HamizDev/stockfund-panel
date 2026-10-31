import type { MinuteKlineRow } from '@/lib/api'
import { computeIntradayAverage, formatMinuteTime, FULL_DAY_TIMES } from '@/lib/intraday-chart'
import { formatKronosTime } from './chartModel'
import type { KronosBar, KronosForecastBar, KronosResult } from './types'
import type { KronosEvaluationPoint } from './evaluationTypes'

export const KRONOS_SESSION_BREAK_PREFIX = '__kronos_session_break__:'

export interface KronosMinuteChartModel {
  categories: string[]
  historyClose: (number | null)[]
  forecastClose: (number | null)[]
  realizedClose: (number | null)[]
  realizedByTime: Map<string, number>
  volumes: ({ value: number; itemStyle: { color: string } } | null)[]
  vwap: (number | null)[]
  historyByTime: Map<string, KronosBar>
  forecastByTime: Map<string, KronosForecastBar>
  categoryIndexByTime: Map<string, number>
  dates: string[]
  latestHistoryTime: string | null
  forecastStartTime: string | null
  forecastEndTime: string | null
  hasVwap: boolean
}

const SESSION_TIMES = new Set(FULL_DAY_TIMES)
const UP_COLOR = '#C74040'
const DOWN_COLOR = '#2D9B65'
const NEUTRAL_COLOR = '#A1A1AA'

function validPrice(value: number): boolean {
  return Number.isFinite(value) && value > 0
}

function normalizeMinuteTime(value: string): string | null {
  const normalized = formatKronosTime(value, '1m')
  const date = normalized.slice(0, 10)
  const minute = formatMinuteTime(normalized)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !SESSION_TIMES.has(minute)) return null
  return `${date} ${minute}`
}

function normalizeHistory(rows: KronosBar[]): Map<string, KronosBar> {
  const sorted = rows
    .filter(row => validPrice(row.open) && validPrice(row.high) && validPrice(row.low) && validPrice(row.close))
    .flatMap(row => {
      const time = normalizeMinuteTime(row.time)
      return time ? [{ time, row }] : []
    })
    .sort((left, right) => left.time.localeCompare(right.time))

  const result = new Map<string, KronosBar>()
  for (const item of sorted) result.set(item.time, item.row)
  return result
}

function normalizeForecast(rows: KronosForecastBar[], after: string | null): Map<string, KronosForecastBar> {
  const sorted = rows
    .filter(row => validPrice(row.close))
    .flatMap(row => {
      const time = normalizeMinuteTime(row.time)
      return time && (!after || time > after) ? [{ time, row }] : []
    })
    .sort((left, right) => left.time.localeCompare(right.time))

  const result = new Map<string, KronosForecastBar>()
  for (const item of sorted) {
    if (!result.has(item.time)) result.set(item.time, item.row)
  }
  return result
}

function buildVwapByTime(historyByTime: Map<string, KronosBar>): Map<string, number> {
  const byDate = new Map<string, Array<{ time: string; row: KronosBar }>>()
  for (const [time, row] of historyByTime) {
    const date = time.slice(0, 10)
    const rows = byDate.get(date) ?? []
    rows.push({ time, row })
    byDate.set(date, rows)
  }

  const vwapByTime = new Map<string, number>()
  for (const rows of byDate.values()) {
    if (rows.some(({ row }) => !Number.isFinite(row.volume) || row.volume! < 0
      || !Number.isFinite(row.amount) || row.amount! < 0)) continue

    const minuteRows: MinuteKlineRow[] = rows.map(({ row, time }) => ({
      datetime: time,
      open: row.open,
      high: row.high,
      low: row.low,
      close: row.close,
      volume: row.volume!,
      amount: row.amount!,
    }))
    computeIntradayAverage(minuteRows).forEach((average, index) => {
      if (average !== null && validPrice(average)) vwapByTime.set(rows[index].time, average)
    })
  }
  return vwapByTime
}

/** Build a full A-share session axis while retaining a unique date on every trading minute. */
export function buildKronosMinuteChartModel(
  result: KronosResult,
  actualPoints: KronosEvaluationPoint[] = [],
): KronosMinuteChartModel {
  const historyRows = result.chart_context?.length ? result.chart_context : result.history
  const historyByTime = normalizeHistory(historyRows)
  const latestHistoryTime = [...historyByTime.keys()].at(-1) ?? null
  const forecastByTime = normalizeForecast(result.forecast, latestHistoryTime)
  // Only overlay evaluated targets; these prices must never enter the frozen model input.
  const realizedByTime = new Map<string, number>()
  const duplicateTimes = new Set<string>()
  for (const point of actualPoints) {
    const time = normalizeMinuteTime(point.time)
    const forecast = time ? forecastByTime.get(time) : undefined
    if (!time || !forecast || point.status !== 'matched' || point.actual_close === null
      || !validPrice(point.actual_close) || point.predicted_close !== forecast.close) continue
    if (realizedByTime.has(time)) duplicateTimes.add(time)
    realizedByTime.set(time, point.actual_close)
  }
  for (const time of duplicateTimes) realizedByTime.delete(time)
  const dates = [...new Set([
    ...[...historyByTime.keys()].map(time => time.slice(0, 10)),
    ...[...forecastByTime.keys()].map(time => time.slice(0, 10)),
  ])].sort()

  const categories: string[] = []
  dates.forEach((date, dateIndex) => {
    if (dateIndex > 0) categories.push(`${KRONOS_SESSION_BREAK_PREFIX}${date}`)
    for (const minute of FULL_DAY_TIMES) categories.push(`${date} ${minute}`)
  })

  const categoryIndexByTime = new Map<string, number>()
  categories.forEach((category, index) => {
    if (!category.startsWith(KRONOS_SESSION_BREAK_PREFIX)) categoryIndexByTime.set(category, index)
  })

  const vwapByTime = buildVwapByTime(historyByTime)
  const historyClose = categories.map(category => historyByTime.get(category)?.close ?? null)
  const volumes = categories.map(category => {
    const row = historyByTime.get(category)
    if (!row || typeof row.volume !== 'number' || !Number.isFinite(row.volume) || row.volume < 0) return null
    return {
      value: row.volume,
      itemStyle: {
        color: row.close > row.open ? UP_COLOR : row.close < row.open ? DOWN_COLOR : NEUTRAL_COLOR,
      },
    }
  })
  const vwap = categories.map(category => vwapByTime.get(category) ?? null)
  const forecastClose = categories.map(category => forecastByTime.get(category)?.close ?? null)
  const realizedClose = categories.map(category => realizedByTime.get(category) ?? null)
  const firstForecastTime = [...forecastByTime.keys()][0] ?? null
  const lastHistoryDate = latestHistoryTime?.slice(0, 10)
  if (firstForecastTime && latestHistoryTime && firstForecastTime.slice(0, 10) === lastHistoryDate) {
    const historyIndex = categoryIndexByTime.get(latestHistoryTime)
    if (historyIndex !== undefined) forecastClose[historyIndex] = historyByTime.get(latestHistoryTime)!.close
    if (historyIndex !== undefined && realizedByTime.size > 0) {
      realizedClose[historyIndex] = historyByTime.get(latestHistoryTime)!.close
    }
  }

  return {
    categories,
    historyClose,
    forecastClose,
    realizedClose,
    realizedByTime,
    volumes,
    vwap,
    historyByTime,
    forecastByTime,
    categoryIndexByTime,
    dates,
    latestHistoryTime,
    forecastStartTime: firstForecastTime,
    forecastEndTime: [...forecastByTime.keys()].at(-1) ?? null,
    hasVwap: vwapByTime.size > 0,
  }
}
