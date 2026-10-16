export interface DonchianCandle {
  high: number
  low: number
}

export interface DonchianChannels {
  upper: Array<number | null>
  lower: Array<number | null>
  middle: Array<number | null>
}

function isValidCandle(candle: DonchianCandle | undefined): candle is DonchianCandle {
  return !!candle && Number.isFinite(candle.high) && Number.isFinite(candle.low) && candle.high >= candle.low
}

/**
 * Rolling Donchian extrema over the current and preceding period - 1 candles.
 * The first period - 1 outputs are null. Invalid OHLC rows make every window
 * containing them null; input candles are never sorted or modified.
 */
export function calculateDonchianChannels(
  candles: readonly DonchianCandle[],
  period = 20,
): DonchianChannels {
  const length = candles.length
  const upper: Array<number | null> = new Array(length).fill(null)
  const lower: Array<number | null> = new Array(length).fill(null)
  const middle: Array<number | null> = new Array(length).fill(null)
  if (!Number.isInteger(period) || period < 1) return { upper, lower, middle }

  const maxDeque: number[] = []
  const minDeque: number[] = []
  let maxHead = 0
  let minHead = 0
  let invalidCount = 0
  for (let index = 0; index < length; index += 1) {
    const candle = candles[index]
    if (isValidCandle(candle)) {
      while (maxDeque.length > maxHead && candles[maxDeque[maxDeque.length - 1]].high <= candle.high) maxDeque.pop()
      maxDeque.push(index)
      while (minDeque.length > minHead && candles[minDeque[minDeque.length - 1]].low >= candle.low) minDeque.pop()
      minDeque.push(index)
    } else {
      invalidCount += 1
    }

    const firstIndex = index - period + 1
    if (firstIndex > 0 && !isValidCandle(candles[firstIndex - 1])) invalidCount -= 1
    while (maxHead < maxDeque.length && maxDeque[maxHead] < firstIndex) maxHead += 1
    while (minHead < minDeque.length && minDeque[minHead] < firstIndex) minHead += 1
    if (maxHead > 64 && maxHead * 2 > maxDeque.length) {
      maxDeque.splice(0, maxHead)
      maxHead = 0
    }
    if (minHead > 64 && minHead * 2 > minDeque.length) {
      minDeque.splice(0, minHead)
      minHead = 0
    }

    if (firstIndex < 0 || invalidCount > 0 || maxHead >= maxDeque.length || minHead >= minDeque.length) continue
    const high = candles[maxDeque[maxHead]].high
    const low = candles[minDeque[minHead]].low
    upper[index] = high
    lower[index] = low
    middle[index] = (high + low) / 2
  }
  return { upper, lower, middle }
}
