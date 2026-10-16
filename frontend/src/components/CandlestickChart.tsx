import { useMemo } from 'react'
import { CanvasMarketChart } from '@/components/CanvasMarketChart'
import { normalizeTradingViewDailyRows } from './tradingview-daily'

export interface OHLC {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume?: number | null
}

export function fmtBigNum(v: number): string {
  if (v >= 1_000_000_000_000) return `${(v / 1_000_000_000_000).toFixed(2)}万亿`
  if (v >= 100_000_000) return `${(v / 100_000_000).toFixed(2)}亿`
  if (v >= 10_000) return `${(v / 10_000).toFixed(0)}万`
  return v.toFixed(0)
}

interface Props {
  data: OHLC[]
  height?: number
  /** Optional series identity for callers that reuse this compact chart. */
  symbol?: string
  contextKey?: string
  title?: string
}

export function CandlestickChart({ data, height = 480, symbol = '指数', contextKey, title = '走势' }: Props) {
  const bars = useMemo(() => normalizeTradingViewDailyRows(data).bars.map(row => ({
    time: row.date,
    open: row.open,
    high: row.high,
    low: row.low,
    close: row.close,
    volume: row.volume ?? null,
  })), [data])

  return <CanvasMarketChart
    bars={bars}
    symbol={symbol}
    title={title}
    contextKey={contextKey ?? symbol}
    height={height}
    visibleBars={60}
    showControls={false}
    upColor="#F04438"
    downColor="#12B76A"
    formatPrice={value => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'}
  />
}
