export type MultiDayZoomRange = { start: number; end: number }

export const FULL_MULTI_DAY_ZOOM: MultiDayZoomRange = { start: 0, end: 100 }

export function resolveMultiDayZoomState(
  previousTradingDaySetKey: string | null,
  dates: readonly string[],
  savedRange: MultiDayZoomRange | null,
): { tradingDaySetKey: string; range: MultiDayZoomRange; contextChanged: boolean } {
  const tradingDaySetKey = JSON.stringify([...new Set(dates)].sort())
  const contextChanged = previousTradingDaySetKey !== tradingDaySetKey
  const savedRangeIsValid = savedRange != null
    && Number.isFinite(savedRange.start)
    && Number.isFinite(savedRange.end)
    && savedRange.start >= 0
    && savedRange.start < savedRange.end
    && savedRange.end <= 100

  return {
    tradingDaySetKey,
    contextChanged,
    range: contextChanged || !savedRangeIsValid
      ? { ...FULL_MULTI_DAY_ZOOM }
      : { ...savedRange },
  }
}
