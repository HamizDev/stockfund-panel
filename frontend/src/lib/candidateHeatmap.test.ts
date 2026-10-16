import { describe, expect, it } from 'vitest'
import {
  candidateHeatColor,
  candidateHeatmapStats,
  layoutCandidateHeatmap,
  type CandidateHeatmapItem,
} from './candidateHeatmap'

function candidate(
  symbol: string,
  rank: number,
  amount: number | null,
  changePct: number | null = 0,
  priceSource: CandidateHeatmapItem['priceSource'] = 'daily',
): CandidateHeatmapItem {
  return { symbol, name: symbol, rank, amount, changePct, priceSource }
}

function expectInside(cells: ReturnType<typeof layoutCandidateHeatmap>, width: number, height: number) {
  for (const cell of cells) {
    expect([cell.x, cell.y, cell.width, cell.height].every(Number.isFinite)).toBe(true)
    expect(cell.x).toBeGreaterThanOrEqual(0)
    expect(cell.y).toBeGreaterThanOrEqual(0)
    expect(cell.width).toBeGreaterThanOrEqual(0)
    expect(cell.height).toBeGreaterThanOrEqual(0)
    expect(cell.x + cell.width).toBeLessThanOrEqual(width)
    expect(cell.y + cell.height).toBeLessThanOrEqual(height)
  }
}

describe('candidate heatmap layout', () => {
  it('uses equal areas for every candidate, including missing amounts', () => {
    const rows = [candidate('A', 1, 100), candidate('B', 2, null), candidate('C', 3, 0)]
    const cells = layoutCandidateHeatmap(rows, 90, 60, 'equal')

    expect(cells).toHaveLength(3)
    expect(cells.map(cell => cell.item.rank)).toEqual([1, 2, 3])
    expect(cells.map(cell => cell.width * cell.height)).toEqual([1800, 1800, 1800])
    expectInside(cells, 90, 60)
  })

  it('preserves the exact 3:1 area ratio without lifting small weights', () => {
    const smaller = candidate('small', 1, 1)
    const larger = candidate('large', 2, 3)
    const cells = layoutCandidateHeatmap([smaller, larger], 40, 20, 'amount')
    const largeCell = cells.find(cell => cell.item.symbol === 'large')!
    const smallCell = cells.find(cell => cell.item.symbol === 'small')!

    expect(cells).toHaveLength(2)
    expect(largeCell.width * largeCell.height / (smallCell.width * smallCell.height)).toBeCloseTo(3)
    expect(largeCell.item.rank).toBe(2)
    expect(smallCell.item.rank).toBe(1)
    expectInside(cells, 40, 20)
  })

  it('filters invalid or missing amounts and keeps equal-weight ties stable', () => {
    const first = candidate('first', 4, 20)
    const second = candidate('second', 2, 20)
    const rows = [
      first,
      candidate('missing', 3, null),
      second,
      candidate('zero', 5, 0),
      candidate('negative', 6, -1),
      candidate('nan', 7, Number.NaN),
      candidate('infinite', 8, Number.POSITIVE_INFINITY),
    ]
    const cells = layoutCandidateHeatmap(rows, 100, 40, 'amount')

    expect(cells.map(cell => cell.item)).toEqual([first, second])
    expect(cells.map(cell => cell.item.rank)).toEqual([4, 2])
    expect(cells.map(cell => cell.width * cell.height)).toEqual([2000, 2000])
  })

  it('keeps finite geometry when a weight ratio is below screen precision', () => {
    const rows = [candidate('huge', 1, Number.MAX_VALUE), candidate('tiny', 2, Number.MIN_VALUE)]
    const cells = layoutCandidateHeatmap(rows, 1e100, 1e100, 'amount')

    expect(cells).toHaveLength(2)
    expectInside(cells, 1e100, 1e100)
  })

  it('returns no cells for empty input or invalid geometry', () => {
    const row = [candidate('A', 1, 1)]
    expect(layoutCandidateHeatmap([], 100, 50, 'equal')).toEqual([])
    expect(layoutCandidateHeatmap(row, 0, 50, 'equal')).toEqual([])
    expect(layoutCandidateHeatmap(row, 100, Number.NaN, 'equal')).toEqual([])
    expect(layoutCandidateHeatmap(row, Number.POSITIVE_INFINITY, 50, 'equal')).toEqual([])
    expect(layoutCandidateHeatmap(row, -1, 50, 'equal')).toEqual([])
    expect(layoutCandidateHeatmap(row, Number.MAX_VALUE, 2, 'equal')).toEqual([])
  })

  it('does not mutate the input array or candidate objects', () => {
    const rows = [candidate('A', 3, 30), candidate('B', 1, 10), candidate('C', 2, null)]
    const before = JSON.stringify(rows)

    layoutCandidateHeatmap(rows, 300, 120, 'amount')
    layoutCandidateHeatmap(rows, 300, 120, 'equal')

    expect(JSON.stringify(rows)).toBe(before)
  })
})

describe('candidate heatmap stats and colors', () => {
  it('counts missing and non-finite returns as unknown, not flat', () => {
    const stats = candidateHeatmapStats([
      candidate('up', 1, 10, 0.01, 'live'),
      candidate('down', 2, 10, -0.01, 'daily'),
      candidate('flat', 3, 10, 0, 'daily'),
      candidate('missing', 4, null, null, 'live'),
      candidate('invalid', 5, null, Number.NaN, 'daily'),
    ])

    expect(stats).toEqual({ total: 5, up: 1, down: 1, flat: 1, unknown: 2, live: 2, daily: 3 })
  })

  it('uses a fixed ±5% scale with red gains, green losses, and neutral unknowns', () => {
    expect(candidateHeatColor(0.01)).toBe('rgb(83,62,74)')
    expect(candidateHeatColor(0.05)).toBe('rgb(185,48,48)')
    expect(candidateHeatColor(0.5)).toBe(candidateHeatColor(0.05))
    expect(candidateHeatColor(-0.05)).toBe('rgb(18,123,82)')
    expect(candidateHeatColor(null)).toBe(candidateHeatColor(0))
    expect(candidateHeatColor(Number.NaN)).toBe(candidateHeatColor(0))
  })
})
