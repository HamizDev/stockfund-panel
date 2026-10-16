import { describe, expect, it } from 'vitest'
import {
  canvasIndexAtX,
  defaultCanvasViewport,
  panCanvasViewport,
  resolveCanvasPriceRange,
  resolveCanvasViewport,
  zoomCanvasViewport,
} from './math'

describe('Canvas chart viewport math', () => {
  it('keeps empty, one-bar, and short histories within valid bounds', () => {
    expect(defaultCanvasViewport(0)).toEqual({ endIndex: -1, visibleCount: 0 })
    expect(defaultCanvasViewport(1)).toEqual({ endIndex: 0, visibleCount: 1 })
    expect(defaultCanvasViewport(4, 60)).toEqual({ endIndex: 3, visibleCount: 4 })
    for (const total of [1, 2, 3, 4]) {
      const initial = defaultCanvasViewport(total)
      const zoomed = zoomCanvasViewport(initial, 1.5, total, 0.5)
      const panned = panCanvasViewport(zoomed, 100, total)
      expect(panned.visibleCount).toBe(total)
      expect(panned.endIndex).toBe(total - 1)
    }
  })

  it('uses visible candles and finite overlays for a padded price range', () => {
    const candles = [
      { low: 9, high: 12 },
      { low: 10, high: 11 },
      { low: 20, high: 22 },
    ]
    const range = resolveCanvasPriceRange(candles, { endIndex: 1, visibleCount: 2 }, [null, Number.NaN, 13])
    expect(range.startIndex).toBe(0)
    expect(range.endIndex).toBe(1)
    expect(range.low).toBeLessThan(9)
    expect(range.high).toBeGreaterThan(13)
    expect(resolveCanvasPriceRange([], { endIndex: -1, visibleCount: 0 })).toEqual({
      startIndex: 0, endIndex: -1, low: 0, high: 1,
    })
  })

  it('follows appended candles only when already at the latest edge and resets context', () => {
    expect(resolveCanvasViewport({
      previous: { endIndex: 19, visibleCount: 8 }, previousCount: 20, nextCount: 23,
      contextChanged: false, visibleBars: 60,
    })).toEqual({ endIndex: 22, visibleCount: 8 })
    expect(resolveCanvasViewport({
      previous: { endIndex: 12, visibleCount: 8 }, previousCount: 20, nextCount: 23,
      contextChanged: false, visibleBars: 60,
    })).toEqual({ endIndex: 12, visibleCount: 8 })
    expect(resolveCanvasViewport({
      previous: { endIndex: 12, visibleCount: 8 }, previousCount: 20, nextCount: 23,
      contextChanged: true, visibleBars: 4,
    })).toEqual({ endIndex: 22, visibleCount: 4 })
  })

  it('keeps a historical viewport anchored to its right-edge candle across rolling windows', () => {
    expect(resolveCanvasViewport({
      previous: { endIndex: 4, visibleCount: 3 }, previousCount: 10, nextCount: 10,
      previousTimes: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
      nextTimes: [2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
      contextChanged: false, visibleBars: 60,
    })).toEqual({ endIndex: 3, visibleCount: 3 })

    expect(resolveCanvasViewport({
      previous: { endIndex: 2, visibleCount: 2 }, previousCount: 5, nextCount: 3,
      previousTimes: [1, 2, 3, 4, 5], nextTimes: [6, 7, 8],
      contextChanged: false, visibleBars: 60,
    })).toEqual({ endIndex: 2, visibleCount: 2 })

    const previousTimes = Array.from({ length: 300 }, (_, index) => index)
    expect(resolveCanvasViewport({
      previous: { endIndex: 110, visibleCount: 100 }, previousCount: 300, nextCount: 280,
      previousTimes, nextTimes: previousTimes.slice(20),
      contextChanged: false, visibleBars: 60,
    })).toEqual({ endIndex: 99, visibleCount: 100 })
  })

  it('zooms around the pointer and clamps pan at the oldest/newest edges', () => {
    const start = { endIndex: 39, visibleCount: 20 }
    const zoomed = zoomCanvasViewport(start, 2, 40, 0.5)
    expect(zoomed.visibleCount).toBe(10)
    const oldAnchor = 20 + 19 * 0.5
    const newStart = zoomed.endIndex - zoomed.visibleCount + 1
    expect(Math.abs((newStart + (zoomed.visibleCount - 1) * 0.5) - oldAnchor)).toBeLessThanOrEqual(0.5)
    expect(panCanvasViewport(start, 100, 40).endIndex).toBe(19)
    expect(panCanvasViewport(start, -100, 40).endIndex).toBe(39)
    expect(canvasIndexAtX({ x: 56, plotLeft: 10, plotRight: 90, startIndex: 4, endIndex: 7 })).toBe(6)
    expect(canvasIndexAtX({ x: 4, plotLeft: 10, plotRight: 90, startIndex: 4, endIndex: 7 })).toBeNull()
  })
})
