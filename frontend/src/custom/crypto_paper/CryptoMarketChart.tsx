import { useMemo } from 'react'
import { CanvasMarketChart, type CanvasChartMarker, type CanvasChartPriceLine, type CanvasChartSeries } from '@/components/CanvasMarketChart'
import { calculateDonchianChannels } from '@/lib/canvas-chart/donchian'
import {
  buildTradeMarkers,
  calculateEma,
  formatBeijingTime,
  normalizeMarketBars,
  type CryptoChartInterval,
  type CryptoChartMarket,
  type CryptoMarketBar,
  type CryptoMarketTrade,
} from './market-chart'

export type { CryptoChartInterval, CryptoChartMarket, CryptoMarketBar, CryptoMarketTrade } from './market-chart'

export interface CryptoMarketChartLevel {
  price: number
  label: string
  color: string
}

export interface CryptoMarketChartProps {
  bars: CryptoMarketBar[]
  interval: CryptoChartInterval
  symbol: string
  trades?: CryptoMarketTrade[]
  levels?: CryptoMarketChartLevel[]
  height?: number
  /** USDM is the default because the strategy paper accounts trade perpetuals. */
  market?: CryptoChartMarket
}

function chartHeight(height: number): number {
  return Number.isFinite(height) && height > 0 ? Math.round(height) : 400
}

function cryptoPrice(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('en-US', { maximumFractionDigits: 8 })
    : '—'
}

export function CryptoMarketChart({
  bars,
  interval,
  symbol,
  trades = [],
  levels = [],
  height = 400,
  market = 'usdm',
}: CryptoMarketChartProps) {
  const validBars = useMemo(() => normalizeMarketBars(bars), [bars])
  const tradeMarkers = useMemo(() => buildTradeMarkers(trades, validBars, interval, symbol, market), [
    trades, validBars, interval, symbol, market,
  ])
  const ema20 = useMemo(() => calculateEma(validBars, 20), [validBars])
  const ema60 = useMemo(() => calculateEma(validBars, 60), [validBars])
  const donchian20 = useMemo(() => calculateDonchianChannels(validBars), [validBars])
  const canvasBars = useMemo(() => validBars.map(bar => ({
    time: bar.time,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume,
  })), [validBars])
  const series = useMemo<CanvasChartSeries[]>(() => [
    { id: 'dc20-upper', label: 'DC20上', color: '#a78bfa', values: donchian20.upper, visibleByDefault: false },
    { id: 'dc20-lower', label: 'DC20下', color: '#c4b5fd', values: donchian20.lower, visibleByDefault: false },
    { id: 'ema20', label: 'EMA20', color: '#fbbf24', values: ema20.map(point => point.value), visibleByDefault: false },
    { id: 'ema60', label: 'EMA60', color: '#38bdf8', values: ema60.map(point => point.value), visibleByDefault: false },
  ], [donchian20, ema20, ema60])
  const markers = useMemo<CanvasChartMarker[]>(() => tradeMarkers.map(marker => ({
    time: marker.time,
    position: marker.position === 'aboveBar' ? 'above' : 'below',
    shape: 'arrow',
    color: marker.color,
    label: marker.text,
  })), [tradeMarkers])
  const priceLines = useMemo<CanvasChartPriceLine[]>(() => levels
    .filter(level => Number.isFinite(level.price) && level.price > 0 && level.label.trim() !== '' && level.color.trim() !== '')
    .map(level => ({ value: level.price, label: level.label, color: level.color, dash: 'dashed' })), [levels])
  const visibleHeight = chartHeight(height)

  return <CanvasMarketChart
    bars={canvasBars}
    symbol={symbol}
    title={interval}
    contextKey={`${market}:${symbol}:${interval}`}
    height={visibleHeight}
    visibleBars={120}
    showControls
    series={series}
    markers={markers}
    priceLines={priceLines}
    upColor="#22c55e"
    downColor="#ef4444"
    formatPrice={cryptoPrice}
    formatTime={(time, includeYear) => formatBeijingTime(time, includeYear)}
  />
}
