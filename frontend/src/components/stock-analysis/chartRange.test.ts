import { describe, expect, it } from 'vitest'
// ECharts uses these runtime internals for category zoom but does not ship their .d.ts files.
// @ts-expect-error ECharts internal JavaScript module has no declaration file.
import OrdinalScale from 'echarts/lib/scale/Ordinal.js'
// @ts-expect-error ECharts internal JavaScript module has no declaration file.
import { linearMap } from 'echarts/lib/util/number.js'
import {
  dateRangeToIndexRange,
  indexRangeToDateRange,
  indexRangeToZoomRange,
  matchingRangePreset,
  presetToIndexRange,
  zoomRangeToIndexRange,
} from './chartRange'

describe('chart date ranges', () => {
  it('anchors shortcuts to the latest loaded bar and uses calendar months', () => {
    const dates = ['2026-01-31', '2026-02-28', '2026-03-01', '2026-03-31', '2026-04-01']

    expect(presetToIndexRange(dates, '1m')).toEqual({ start: 2, end: 4 })
    expect(presetToIndexRange(dates, 'all')).toEqual({ start: 0, end: 4 })
  })

  it('normalizes reversed dates and resolves a weekend-only selection to one loaded bar', () => {
    const dates = ['2026-10-15', '2026-10-16', '2026-10-19', '2026-10-20']

    expect(dateRangeToIndexRange(dates, { start: '2026-10-20', end: '2026-10-16' }))
      .toEqual({ start: 1, end: 3 })
    expect(dateRangeToIndexRange(dates, { start: '2026-10-17', end: '2026-10-18' }))
      .toEqual({ start: 2, end: 2 })
  })

  it('maps index ranges to displayed dates and zoom percentages, then back', () => {
    const dates = ['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15']
    const indices = { start: 1, end: 2 }
    const zoom = indexRangeToZoomRange(indices, dates.length)

    expect(indexRangeToDateRange(dates, indices)).toEqual({ start: '2026-10-13', end: '2026-10-14' })
    expect(zoom.start).toBeCloseTo(100 / 3, 12)
    expect(zoom.end).toBeCloseTo(200 / 3, 12)
    expect(zoomRangeToIndexRange(zoom, dates.length)).toEqual(indices)
    expect(matchingRangePreset(dates, indices)).toBeNull()
  })

  it('handles empty and single-bar datasets without invalid indices', () => {
    expect(presetToIndexRange([], '6m')).toBeNull()
    expect(dateRangeToIndexRange([], { start: '', end: '' })).toBeNull()
    expect(indexRangeToDateRange([], { start: 0, end: 0 })).toBeNull()
    expect(zoomRangeToIndexRange({ start: 30, end: 70 }, 0)).toBeNull()

    const single = ['2026-10-16']
    expect(presetToIndexRange(single, 'all')).toEqual({ start: 0, end: 0 })
    expect(indexRangeToZoomRange({ start: 0, end: 0 }, 1)).toEqual({ start: 0, end: 100 })
    expect(zoomRangeToIndexRange({ start: 30, end: 70 }, 1)).toEqual({ start: 0, end: 0 })
    expect(matchingRangePreset(single, { start: 0, end: 0 })).toBeNull()
  })

  it('normalizes reversed and out-of-bounds zoom percentages', () => {
    expect(zoomRangeToIndexRange({ start: 90, end: 20 }, 10)).toEqual({ start: 2, end: 8 })
    expect(zoomRangeToIndexRange({ start: -10, end: 150 }, 10)).toEqual({ start: 0, end: 9 })
  })

  it('matches ECharts category dataZoom mapping on a long loaded dataset', () => {
    const length = 250
    const lastIndex = length - 1
    const scale = new OrdinalScale({
      extent: [0, lastIndex],
      ordinalMeta: { categories: Array.from({ length }, (_, index) => String(index)) },
    })
    const ranges = [
      { start: 0, end: 249 },
      { start: 1, end: 37 },
      { start: 83, end: 194 },
      { start: 248, end: 249 },
    ]

    for (const range of ranges) {
      const zoom = indexRangeToZoomRange(range, length)
      expect(zoom.start).toBeCloseTo(linearMap(range.start, [0, lastIndex], [0, 100]), 12)
      expect(zoom.end).toBeCloseTo(linearMap(range.end, [0, lastIndex], [0, 100]), 12)
      expect(scale.parse(linearMap(zoom.start, [0, 100], [0, lastIndex]))).toBe(range.start)
      expect(scale.parse(linearMap(zoom.end, [0, 100], [0, lastIndex]))).toBe(range.end)
      expect(zoomRangeToIndexRange(zoom, length)).toEqual(range)
    }

    const draggedZoom = { start: 17.3, end: 68.7 }
    const echartsMappedRange = {
      start: scale.parse(linearMap(draggedZoom.start, [0, 100], [0, lastIndex])),
      end: scale.parse(linearMap(draggedZoom.end, [0, 100], [0, lastIndex])),
    }
    expect(zoomRangeToIndexRange(draggedZoom, length)).toEqual(echartsMappedRange)
  })
})
