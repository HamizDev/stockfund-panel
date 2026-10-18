export interface CanvasViewport {
  /** Inclusive right edge of the visible candle window. */
  endIndex: number
  visibleCount: number
}

export interface StoredCanvasViewport {
  endTime: string | number
  visibleCount: number
  atLatest: boolean
}

export interface CanvasViewportMemory {
  read: (key: string) => StoredCanvasViewport | null
  write: (key: string, value: StoredCanvasViewport) => void
}

export interface CanvasPriceRange {
  startIndex: number
  endIndex: number
  low: number
  high: number
}

export interface CanvasCandleRange {
  low: number
  high: number
}

export function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), high)
}

export function defaultCanvasViewport(total: number, requested: number | 'all' = 60): CanvasViewport {
  if (total <= 0) return { endIndex: -1, visibleCount: 0 }
  const visibleCount = requested === 'all'
    ? total
    : Number.isFinite(requested)
      ? Math.min(total, Math.max(1, Math.floor(requested)))
      : Math.min(total, 60)
  return { endIndex: total - 1, visibleCount }
}

export function resolveCanvasPriceRange(
  candles: readonly CanvasCandleRange[],
  state: CanvasViewport,
  extraValues: readonly (number | null | undefined)[] = [],
  paddingRatio = 0.05,
): CanvasPriceRange {
  const total = candles.length
  if (total === 0) return { startIndex: 0, endIndex: -1, low: 0, high: 1 }

  const visibleCount = clamp(Math.floor(state.visibleCount) || 1, 1, total)
  const endIndex = clamp(Math.floor(state.endIndex), visibleCount - 1, total - 1)
  const startIndex = endIndex - visibleCount + 1
  let low = Infinity
  let high = -Infinity

  for (let index = startIndex; index <= endIndex; index += 1) {
    const candle = candles[index]
    if (!candle || !Number.isFinite(candle.low) || !Number.isFinite(candle.high)) continue
    low = Math.min(low, candle.low)
    high = Math.max(high, candle.high)
  }
  for (const value of extraValues) {
    if (typeof value !== 'number' || !Number.isFinite(value)) continue
    low = Math.min(low, value)
    high = Math.max(high, value)
  }

  if (!Number.isFinite(low) || !Number.isFinite(high)) return { startIndex, endIndex, low: 0, high: 1 }
  const span = high - low
  const padding = span * Math.max(0, paddingRatio) || Math.abs(low) * Math.max(0, paddingRatio) || 1
  return { startIndex, endIndex, low: low - padding, high: high + padding }
}

/**
 * Keep the same visible count when the user is browsing history. When the old
 * window touched the latest candle, shift it forward with appended data.
 */
export function resolveCanvasViewport(input: {
  previous: CanvasViewport | null
  previousCount: number
  nextCount: number
  previousTimes?: readonly (string | number)[]
  nextTimes?: readonly (string | number)[]
  contextChanged: boolean
  visibleBars: number | 'all'
}): CanvasViewport {
  const { previous, previousCount, nextCount, previousTimes, nextTimes, contextChanged, visibleBars } = input
  if (nextCount <= 0) return { endIndex: -1, visibleCount: 0 }
  if (contextChanged || !previous || previousCount <= 0) return defaultCanvasViewport(nextCount, visibleBars)

  const visibleCount = clamp(Math.floor(previous.visibleCount) || 1, 1, nextCount)
  const previousEnd = Math.floor(previous.endIndex)
  const wasAtLatest = previousEnd >= previousCount - 1
  if (wasAtLatest) return { endIndex: nextCount - 1, visibleCount }

  if (!Number.isFinite(previousEnd) || previousEnd < 0) return defaultCanvasViewport(nextCount, visibleBars)
  const oldEnd = clamp(previousEnd, 0, previousCount - 1)
  const anchorTime = previousTimes?.[oldEnd]
  const anchoredIndex = anchorTime === undefined ? -1 : nextTimes?.findIndex(time => time === anchorTime) ?? -1
  return {
    endIndex: clamp(anchoredIndex >= 0 ? anchoredIndex : oldEnd, visibleCount - 1, nextCount - 1),
    visibleCount,
  }
}

/** Anchor-preserving zoom; anchorRatio is the pointer's position inside the plot. */
export function zoomCanvasViewport(
  state: CanvasViewport,
  factor: number,
  total: number,
  anchorRatio = 1,
): CanvasViewport {
  if (total <= 0 || !Number.isFinite(factor) || factor <= 0) return { endIndex: -1, visibleCount: 0 }
  const oldCount = clamp(Math.floor(state.visibleCount) || 1, 1, total)
  const minimum = Math.min(5, total)
  const visibleCount = clamp(Math.round(oldCount / factor), minimum, total)
  if (visibleCount === oldCount) return { endIndex: clamp(state.endIndex, oldCount - 1, total - 1), visibleCount }

  const ratio = clamp(Number.isFinite(anchorRatio) ? anchorRatio : 1, 0, 1)
  const oldStart = clamp(state.endIndex, oldCount - 1, total - 1) - oldCount + 1
  const anchorIndex = oldStart + (oldCount - 1) * ratio
  const startIndex = Math.round(anchorIndex - (visibleCount - 1) * ratio)
  const endIndex = clamp(startIndex + visibleCount - 1, visibleCount - 1, total - 1)
  return { endIndex, visibleCount }
}

/** Positive delta moves the visible window to older candles, matching drag-right. */
export function panCanvasViewport(state: CanvasViewport, deltaBars: number, total: number): CanvasViewport {
  if (total <= 0) return { endIndex: -1, visibleCount: 0 }
  const visibleCount = clamp(Math.floor(state.visibleCount) || 1, 1, total)
  const endIndex = clamp(Math.round(state.endIndex - deltaBars), visibleCount - 1, total - 1)
  return { endIndex, visibleCount }
}

export function canvasIndexAtX(input: {
  x: number
  plotLeft: number
  plotRight: number
  startIndex: number
  endIndex: number
}): number | null {
  const { x, plotLeft, plotRight, startIndex, endIndex } = input
  if (endIndex < startIndex || plotRight <= plotLeft || x < plotLeft || x > plotRight) return null
  const count = endIndex - startIndex + 1
  const slotWidth = (plotRight - plotLeft) / count
  return clamp(Math.floor((x - plotLeft) / slotWidth), 0, count - 1) + startIndex
}
