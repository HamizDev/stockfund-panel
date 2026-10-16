import { calculateDonchianChannels } from './canvas-chart/donchian'

export interface TechnicalSummaryBar {
  time?: string | number
  open: number
  high: number
  low: number
  close: number
  volume?: number | null
}

export type EmaRelation = 'above' | 'below' | 'equal' | 'insufficient'
export type RsiState = 'hot' | 'cold' | 'normal' | 'insufficient'
export type MacdRelation = 'above' | 'below' | 'equal' | 'insufficient'
export type DonchianState = 'breakout-up' | 'breakout-down' | 'inside' | 'insufficient'

export interface LoadedRangePerformance {
  status: 'ready' | 'insufficient' | 'invalid-base' | 'invalid-data'
  returnPct: number | null
  high: number | null
  low: number | null
  maxDrawdownPct: number | null
  startClose: number | null
  endClose: number | null
}

export interface TechnicalSummary {
  validBarCount: number
  timeRange: { start: string | null; end: string | null; orderedByTime: boolean }
  emaTrend: { status: EmaRelation; ema20: number | null; ema60: number | null; requiredBars: number }
  rsi14: { status: RsiState; value: number | null; requiredBars: number }
  macd: {
    status: MacdRelation
    dif: number | null
    dea: number | null
    histogram: number | null
    requiredBars: number
  }
  donchian20: {
    status: DonchianState
    previousUpper: number | null
    previousLower: number | null
    close: number | null
    requiredBars: number
  }
  performance: LoadedRangePerformance
}

/** EMA seeded by the first period-sized SMA. Values before the seed (and invalid gaps) are null. */
export function calculateEma(values: readonly number[], period: number): Array<number | null> {
  const result: Array<number | null> = new Array(values.length).fill(null)
  if (!Number.isInteger(period) || period < 1) return result

  const alpha = 2 / (period + 1)
  let seedSum = 0
  let seedCount = 0
  let previous: number | null = null

  for (let index = 0; index < values.length; index += 1) {
    const value = values[index]
    if (!Number.isFinite(value)) {
      seedSum = 0
      seedCount = 0
      previous = null
      continue
    }

    if (previous === null) {
      seedSum += value
      seedCount += 1
      if (seedCount === period) {
        previous = seedSum / period
        result[index] = previous
      }
      continue
    }

    previous = alpha * value + (1 - alpha) * previous
    result[index] = previous
  }

  return result
}

function calculateRsi(values: readonly number[], period: number): Array<number | null> {
  const result: Array<number | null> = new Array(values.length).fill(null)
  if (!Number.isInteger(period) || period < 1) return result

  let previousClose: number | null = null
  let seedGain = 0
  let seedLoss = 0
  let seedCount = 0
  let averageGain: number | null = null
  let averageLoss: number | null = null

  for (let index = 0; index < values.length; index += 1) {
    const close = values[index]
    if (!Number.isFinite(close)) {
      previousClose = null
      seedGain = 0
      seedLoss = 0
      seedCount = 0
      averageGain = null
      averageLoss = null
      continue
    }
    if (previousClose === null) {
      previousClose = close
      continue
    }

    const change = close - previousClose
    const gain = Math.max(change, 0)
    const loss = Math.max(-change, 0)
    if (averageGain === null || averageLoss === null) {
      seedGain += gain
      seedLoss += loss
      seedCount += 1
      if (seedCount === period) {
        averageGain = seedGain / period
        averageLoss = seedLoss / period
      }
    } else {
      averageGain = (averageGain * (period - 1) + gain) / period
      averageLoss = (averageLoss * (period - 1) + loss) / period
    }

    previousClose = close
    if (averageGain === null || averageLoss === null) continue
    if (averageGain === 0 && averageLoss === 0) result[index] = 50
    else if (averageLoss === 0) result[index] = 100
    else if (averageGain === 0) result[index] = 0
    else result[index] = 100 - 100 / (1 + averageGain / averageLoss)
  }

  return result
}

function validBar(bar: TechnicalSummaryBar | undefined): bar is TechnicalSummaryBar {
  if (!bar) return false
  const { open, high, low, close } = bar
  return [open, high, low, close].every(Number.isFinite)
    && open > 0 && high > 0 && low > 0 && close > 0
    && high >= low && high >= open && high >= close
    && low <= open && low <= close
}

type OrderedBar = { bar: TechnicalSummaryBar; index: number; key: number | string | null; label: string | null; keyType: 'number' | 'date' | null }

function timeKey(time: string | number | undefined): Pick<OrderedBar, 'key' | 'label' | 'keyType'> {
  if (typeof time === 'number' && Number.isFinite(time)) {
    return { key: time, label: String(time), keyType: 'number' }
  }
  if (typeof time !== 'string' || !/^\d{4}-\d{2}-\d{2}(?:$|T)/.test(time)) {
    return { key: null, label: null, keyType: null }
  }
  const day = time.slice(0, 10)
  const parsed = Date.parse(time)
  const dayParsed = Date.parse(day + 'T00:00:00Z')
  if (!Number.isFinite(parsed) || !Number.isFinite(dayParsed) || new Date(dayParsed).toISOString().slice(0, 10) !== day) {
    return { key: null, label: null, keyType: null }
  }
  return { key: parsed, label: day, keyType: 'date' }
}

function orderBars(rows: readonly TechnicalSummaryBar[]) {
  const candidates: OrderedBar[] = []
  rows.forEach((bar, index) => {
    if (!validBar(bar)) return
    candidates.push({ bar, index, ...timeKey(bar.time) })
  })

  const keyType = candidates.length > 0 ? candidates[0].keyType : null
  const canSort = keyType !== null && candidates.every(item => item.keyType === keyType && item.key !== null)
  if (canSort) {
    candidates.sort((left, right) => Number(left.key) - Number(right.key) || left.index - right.index)
  }
  return {
    bars: candidates.map(item => item.bar),
    start: canSort ? candidates[0]?.label ?? null : null,
    end: canSort ? candidates[candidates.length - 1]?.label ?? null : null,
    orderedByTime: canSort,
  }
}

export function calculateLoadedRangePerformance(
  bars: readonly Pick<TechnicalSummaryBar, 'high' | 'low' | 'close'>[],
): LoadedRangePerformance {
  const empty: LoadedRangePerformance = {
    status: 'insufficient', returnPct: null, high: null, low: null,
    maxDrawdownPct: null, startClose: null, endClose: null,
  }
  if (bars.length === 0) return empty

  const firstClose = bars[0].close
  if (firstClose === 0) return { ...empty, status: 'invalid-base' }
  if (!Number.isFinite(firstClose) || firstClose < 0) return { ...empty, status: 'invalid-base' }
  if (bars.some(bar => !Number.isFinite(bar.high) || !Number.isFinite(bar.low) || !Number.isFinite(bar.close)
    || bar.high < bar.low || bar.close <= 0)) {
    return { ...empty, status: 'invalid-data' }
  }

  const lastClose = bars[bars.length - 1].close
  let high = -Infinity
  let low = Infinity
  let peak = firstClose
  let maxDrawdownPct = 0
  for (const bar of bars) {
    high = Math.max(high, bar.high)
    low = Math.min(low, bar.low)
    peak = Math.max(peak, bar.close)
    maxDrawdownPct = Math.min(maxDrawdownPct, (bar.close / peak - 1) * 100)
  }

  return {
    status: 'ready',
    returnPct: (lastClose / firstClose - 1) * 100,
    high,
    low,
    maxDrawdownPct,
    startClose: firstClose,
    endClose: lastClose,
  }
}

export function calculateTechnicalSummary(rows: readonly TechnicalSummaryBar[]): TechnicalSummary {
  const ordered = orderBars(rows)
  const bars = ordered.bars
  const closes = bars.map(bar => bar.close)

  const ema20Values = calculateEma(closes, 20)
  const ema60Values = calculateEma(closes, 60)
  const ema20 = ema20Values[ema20Values.length - 1] ?? null
  const ema60 = ema60Values[ema60Values.length - 1] ?? null
  const emaStatus: EmaRelation = ema20 === null || ema60 === null
    ? 'insufficient'
    : ema20 > ema60 ? 'above' : ema20 < ema60 ? 'below' : 'equal'

  const rsiValues = calculateRsi(closes, 14)
  const rsi14 = rsiValues[rsiValues.length - 1] ?? null
  const rsiStatus: RsiState = rsi14 === null
    ? 'insufficient'
    : rsi14 >= 70 ? 'hot' : rsi14 <= 30 ? 'cold' : 'normal'

  const macdFast = calculateEma(closes, 12)
  const macdSlow = calculateEma(closes, 26)
  const difValues = closes.map((_, index) => {
    const fast = macdFast[index]
    const slow = macdSlow[index]
    return fast === null || slow === null ? Number.NaN : fast - slow
  })
  const deaValues = calculateEma(difValues, 9)
  const lastIndex = bars.length - 1
  const dif = lastIndex >= 0 && Number.isFinite(difValues[lastIndex]) ? difValues[lastIndex] : null
  const dea = deaValues[lastIndex] ?? null
  const histogram = dif !== null && dea !== null ? (dif - dea) * 2 : null
  const macdStatus: MacdRelation = dif === null || dea === null
    ? 'insufficient'
    : dif > dea ? 'above' : dif < dea ? 'below' : 'equal'

  const donchian = calculateDonchianChannels(bars, 20)
  const previousUpper = lastIndex > 0 ? donchian.upper[lastIndex - 1] : null
  const previousLower = lastIndex > 0 ? donchian.lower[lastIndex - 1] : null
  const lastClose = lastIndex >= 0 ? closes[lastIndex] : null
  const donchianStatus: DonchianState = previousUpper === null || previousLower === null || lastClose === null
    ? 'insufficient'
    : lastClose > previousUpper ? 'breakout-up' : lastClose < previousLower ? 'breakout-down' : 'inside'

  return {
    validBarCount: bars.length,
    timeRange: { start: ordered.start, end: ordered.end, orderedByTime: ordered.orderedByTime },
    emaTrend: { status: emaStatus, ema20, ema60, requiredBars: 60 },
    rsi14: { status: rsiStatus, value: rsi14, requiredBars: 15 },
    macd: { status: macdStatus, dif, dea, histogram, requiredBars: 34 },
    donchian20: {
      status: donchianStatus,
      previousUpper: previousUpper ?? null,
      previousLower: previousLower ?? null,
      close: lastClose,
      requiredBars: 21,
    },
    performance: calculateLoadedRangePerformance(bars),
  }
}
