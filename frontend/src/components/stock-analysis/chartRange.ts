export type ChartRangePreset = '1m' | '3m' | '6m' | '1y' | 'all'

export interface ChartIndexRange {
  start: number
  end: number
}

export interface ChartDateRange {
  start: string
  end: string
}

export interface ChartZoomRange {
  start: number
  end: number
}

export const FULL_CHART_ZOOM: ChartZoomRange = { start: 0, end: 100 }

const PRESET_MONTHS: Partial<Record<ChartRangePreset, number>> = {
  '1m': 1,
  '3m': 3,
  '6m': 6,
  '1y': 12,
}

function cleanDate(value: string): string {
  return value.slice(0, 10)
}

function subtractMonths(date: string, months: number): string {
  const [year, month, day] = cleanDate(date).split('-').map(Number)
  const absoluteMonth = year * 12 + month - 1 - months
  const targetYear = Math.floor(absoluteMonth / 12)
  const targetMonth = ((absoluteMonth % 12) + 12) % 12
  const daysInTargetMonth = new Date(Date.UTC(targetYear, targetMonth + 1, 0)).getUTCDate()
  const targetDay = Math.min(day, daysInTargetMonth)
  return [
    String(targetYear).padStart(4, '0'),
    String(targetMonth + 1).padStart(2, '0'),
    String(targetDay).padStart(2, '0'),
  ].join('-')
}

function lowerBound(dates: string[], target: string): number {
  let low = 0
  let high = dates.length
  while (low < high) {
    const mid = Math.floor((low + high) / 2)
    if (cleanDate(dates[mid]) < target) low = mid + 1
    else high = mid
  }
  return low
}

function upperBound(dates: string[], target: string): number {
  let low = 0
  let high = dates.length
  while (low < high) {
    const mid = Math.floor((low + high) / 2)
    if (cleanDate(dates[mid]) <= target) low = mid + 1
    else high = mid
  }
  return low
}

function clampIndexRange(range: ChartIndexRange, length: number): ChartIndexRange {
  const start = Math.max(0, Math.min(length - 1, Math.floor(range.start)))
  const end = Math.max(0, Math.min(length - 1, Math.floor(range.end)))
  return start <= end ? { start, end } : { start, end: start }
}

/** Map explicit dates to loaded rows. Calendar dates without a bar resolve to the nearest later bar. */
export function dateRangeToIndexRange(dates: string[], range: ChartDateRange): ChartIndexRange | null {
  if (dates.length === 0) return null

  let startDate = cleanDate(range.start || dates[0])
  let endDate = cleanDate(range.end || dates[dates.length - 1])
  if (startDate > endDate) [startDate, endDate] = [endDate, startDate]

  let start = Math.min(lowerBound(dates, startDate), dates.length - 1)
  let end = Math.max(0, upperBound(dates, endDate) - 1)
  // A selected interval can fall entirely between two trading days. Keep one visible bar.
  if (start > end) end = start
  return clampIndexRange({ start, end }, dates.length)
}

export function indexRangeToDateRange(dates: string[], range: ChartIndexRange): ChartDateRange | null {
  if (dates.length === 0) return null
  const clamped = clampIndexRange(range, dates.length)
  return { start: cleanDate(dates[clamped.start]), end: cleanDate(dates[clamped.end]) }
}

/** Presets are anchored to the latest loaded bar, so they remain useful after market close or holidays. */
export function presetToIndexRange(dates: string[], preset: ChartRangePreset): ChartIndexRange | null {
  if (dates.length === 0) return null
  if (preset === 'all') return { start: 0, end: dates.length - 1 }
  const months = PRESET_MONTHS[preset]
  if (!months) return { start: 0, end: dates.length - 1 }
  return dateRangeToIndexRange(dates, {
    start: subtractMonths(dates[dates.length - 1], months),
    end: dates[dates.length - 1],
  })
}

export function indexRangeToZoomRange(range: ChartIndexRange, length: number): ChartZoomRange {
  if (length <= 1) return FULL_CHART_ZOOM
  const clamped = clampIndexRange(range, length)
  const lastIndex = length - 1
  return {
    // ECharts maps dataZoom percentages onto a category extent of [0, length - 1],
    // then OrdinalScale.parse rounds each endpoint to its nearest category.
    start: (clamped.start / lastIndex) * 100,
    end: (clamped.end / lastIndex) * 100,
  }
}

export function zoomRangeToIndexRange(zoom: ChartZoomRange, length: number): ChartIndexRange | null {
  if (length === 0) return null
  if (length === 1) return { start: 0, end: 0 }

  let startPercent = Number.isFinite(zoom.start) ? Math.max(0, Math.min(100, zoom.start)) : 0
  let endPercent = Number.isFinite(zoom.end) ? Math.max(0, Math.min(100, zoom.end)) : 100
  if (startPercent > endPercent) [startPercent, endPercent] = [endPercent, startPercent]

  const lastIndex = length - 1
  const start = Math.round((startPercent / 100) * lastIndex)
  const end = Math.round((endPercent / 100) * lastIndex)
  return clampIndexRange({ start, end }, length)
}

export function matchingRangePreset(dates: string[], range: ChartIndexRange): ChartRangePreset | null {
  if (dates.length <= 1) return null
  const presets: ChartRangePreset[] = ['1m', '3m', '6m', '1y', 'all']
  return presets.find(preset => {
    const candidate = presetToIndexRange(dates, preset)
    return candidate?.start === range.start && candidate.end === range.end
  }) ?? null
}
