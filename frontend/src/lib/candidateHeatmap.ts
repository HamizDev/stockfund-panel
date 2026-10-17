export type HeatmapWeight = 'equal' | 'amount'

export interface CandidateHeatmapItem {
  symbol: string
  name: string
  rank: number
  /** Daily return as a fraction: 0.01 means +1%. */
  changePct: number | null
  amount: number | null
  priceSource: 'live' | 'daily'
}

export interface CandidateHeatmapCell {
  x: number
  y: number
  width: number
  height: number
  item: CandidateHeatmapItem
}

type WeightedItem = {
  item: CandidateHeatmapItem
  inputIndex: number
  weight: number
}

/**
 * Lay out candidate tiles without changing their rank or mutating the input.
 * Amount mode only includes finite positive amounts. Equal mode includes every
 * item, including rows with no amount or return.
 *
 * A balanced binary treemap keeps the true area ratios without imposing an
 * artificial minimum on tiny weights. Local normalization
 * avoids overflow when finite amounts are very large.
 */
export function layoutCandidateHeatmap(
  items: readonly CandidateHeatmapItem[],
  width: number,
  height: number,
  weight: HeatmapWeight,
): CandidateHeatmapCell[] {
  if (
    !Number.isFinite(width) ||
    !Number.isFinite(height) ||
    width <= 0 ||
    height <= 0 ||
    !Number.isFinite(width * height) ||
    width * height <= 0
  ) {
    return []
  }

  const weighted: WeightedItem[] = []
  for (let inputIndex = 0; inputIndex < items.length; inputIndex += 1) {
    const item = items[inputIndex]
    if (weight === 'amount') {
      if (typeof item.amount !== 'number' || !Number.isFinite(item.amount) || item.amount <= 0) continue
      weighted.push({ item, inputIndex, weight: item.amount })
    } else if (weight === 'equal') {
      weighted.push({ item, inputIndex, weight: 1 })
    } else {
      return []
    }
  }
  if (weighted.length === 0) return []

  // Stable descending size order improves the treemap while keeping rank on
  // each original item. Equal weights retain the candidate input order.
  weighted.sort((a, b) => b.weight - a.weight || a.inputIndex - b.inputIndex)

  const laidOut = new Array<CandidateHeatmapCell>(weighted.length)

  const splitFor = (start: number, end: number) => {
    let maxWeight = 0
    for (let i = start; i < end; i += 1) maxWeight = Math.max(maxWeight, weighted[i].weight)

    let total = 0
    for (let i = start; i < end; i += 1) total += weighted[i].weight / maxWeight

    let bestIndex = start + 1
    let bestPrefix = 0
    let bestDistance = Number.POSITIVE_INFINITY
    let prefix = 0

    for (let splitIndex = start + 1; splitIndex < end; splitIndex += 1) {
      prefix += weighted[splitIndex - 1].weight / maxWeight
      const distance = Math.abs(prefix - total / 2)
      if (distance < bestDistance) {
        bestDistance = distance
        bestIndex = splitIndex
        bestPrefix = prefix
      }
    }

    return { splitIndex: bestIndex, ratio: bestPrefix / total }
  }

  const visit = (start: number, end: number, x0: number, y0: number, x1: number, y1: number) => {
    if (end - start === 1) {
      const entry = weighted[start]
      laidOut[start] = {
        x: x0,
        y: y0,
        width: x1 - x0,
        height: y1 - y0,
        item: entry.item,
      }
      return
    }

    const { splitIndex, ratio } = splitFor(start, end)
    const regionWidth = x1 - x0
    const regionHeight = y1 - y0

    if (regionWidth >= regionHeight) {
      const splitX = Math.max(x0, Math.min(x1, x0 + regionWidth * ratio))
      visit(start, splitIndex, x0, y0, splitX, y1)
      visit(splitIndex, end, splitX, y0, x1, y1)
    } else {
      const splitY = Math.max(y0, Math.min(y1, y0 + regionHeight * ratio))
      visit(start, splitIndex, x0, y0, x1, splitY)
      visit(splitIndex, end, x0, splitY, x1, y1)
    }
  }

  visit(0, weighted.length, 0, 0, width, height)

  // Return cells in treemap size order, with the original candidate/rank data
  // untouched. The input-order tie-break above makes this deterministic.
  return laidOut
}

export function candidateHeatmapStats(items: readonly CandidateHeatmapItem[]): {
  total: number
  up: number
  down: number
  flat: number
  unknown: number
  live: number
  daily: number
} {
  let up = 0
  let down = 0
  let flat = 0
  let unknown = 0
  let live = 0
  let daily = 0

  for (const item of items) {
    if (item.priceSource === 'live') live += 1
    else if (item.priceSource === 'daily') daily += 1

    if (typeof item.changePct !== 'number' || !Number.isFinite(item.changePct)) {
      unknown += 1
    } else if (item.changePct > 0) {
      up += 1
    } else if (item.changePct < 0) {
      down += 1
    } else {
      flat += 1
    }
  }

  return { total: items.length, up, down, flat, unknown, live, daily }
}

const NEUTRAL = [58, 65, 80] as const
// Keep white labels readable in both existing panel themes.
const UP = [185, 48, 48] as const
const DOWN = [18, 123, 82] as const
const COLOR_SCALE = 0.05

/** Fixed ±5% fractional scale; gains are red, losses green, missing is neutral. */
export function candidateHeatColor(changePct: number | null): string {
  if (typeof changePct !== 'number' || !Number.isFinite(changePct) || changePct === 0) {
    return `rgb(${NEUTRAL.join(',')})`
  }

  const target = changePct > 0 ? UP : DOWN
  const intensity = Math.min(1, Math.abs(changePct) / COLOR_SCALE)
  const channels = NEUTRAL.map((base, index) => Math.round(base + (target[index] - base) * intensity))
  return `rgb(${channels.join(',')})`
}
