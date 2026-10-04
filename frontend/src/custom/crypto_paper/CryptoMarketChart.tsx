import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useChartTheme } from '@/lib/theme'
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

const UP = '#22c55e'
const DOWN = '#ef4444'

function chartHeight(height: number): number {
  return Number.isFinite(height) && height > 0 ? Math.round(height) : 400
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
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const ema20Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ema60Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const priceLinesRef = useRef<IPriceLine[]>([])
  const lastContextRef = useRef<string | null>(null)
  const theme = useChartTheme()
  const themeRef = useRef(theme)
  const heightRef = useRef(chartHeight(height))
  const [showEma20, setShowEma20] = useState(false)
  const [showEma60, setShowEma60] = useState(false)

  themeRef.current = theme
  heightRef.current = chartHeight(height)

  const validBars = useMemo(() => normalizeMarketBars(bars), [bars])
  const markers = useMemo(() => buildTradeMarkers(trades, validBars, interval, symbol, market), [
    trades, validBars, interval, symbol, market,
  ])
  const ema20 = useMemo(() => calculateEma(validBars, 20), [validBars])
  const ema60 = useMemo(() => calculateEma(validBars, 60), [validBars])
  const visibleHeight = chartHeight(height)

  useEffect(() => {
    const element = containerRef.current
    if (!element) return

    const chart = createChart(element, {
      width: Math.max(1, element.clientWidth),
      height: heightRef.current,
      layout: {
        attributionLogo: true,
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: themeRef.current.text,
        fontFamily: 'JetBrains Mono, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: themeRef.current.grid },
        horzLines: { color: themeRef.current.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: themeRef.current.crosshair, labelVisible: true },
        horzLine: { color: themeRef.current.crosshair, labelVisible: true },
      },
      rightPriceScale: {
        borderColor: themeRef.current.border,
        scaleMargins: { top: 0.04, bottom: 0.23 },
      },
      timeScale: {
        borderColor: themeRef.current.border,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: Time) => formatBeijingTime(time),
      },
      localization: {
        timeFormatter: (time: Time) => formatBeijingTime(time, true),
      },
    })

    const candle = chart.addCandlestickSeries({
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      lastValueVisible: false,
      priceLineVisible: false,
    })
    const volume = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      lastValueVisible: false,
      priceLineVisible: false,
    })
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
    const ema20Series = chart.addLineSeries({
      color: '#fbbf24',
      lineWidth: 1,
      lastValueVisible: false,
      priceLineVisible: false,
      visible: false,
    })
    const ema60Series = chart.addLineSeries({
      color: '#38bdf8',
      lineWidth: 1,
      lastValueVisible: false,
      priceLineVisible: false,
      visible: false,
    })

    chartRef.current = chart
    candleRef.current = candle
    volumeRef.current = volume
    ema20Ref.current = ema20Series
    ema60Ref.current = ema60Series

    const resizeObserver = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(entries => {
        const width = Math.floor(entries[0]?.contentRect.width ?? element.clientWidth)
        if (width > 0) chart.applyOptions({ width, height: heightRef.current })
      })
    resizeObserver?.observe(element)

    return () => {
      resizeObserver?.disconnect()
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volumeRef.current = null
      ema20Ref.current = null
      ema60Ref.current = null
      priceLinesRef.current = []
      lastContextRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.applyOptions({
      layout: { textColor: theme.text },
      grid: { vertLines: { color: theme.grid }, horzLines: { color: theme.grid } },
      crosshair: {
        vertLine: { color: theme.crosshair, labelBackgroundColor: theme.crosshairLabelBg },
        horzLine: { color: theme.crosshair, labelBackgroundColor: theme.crosshairLabelBg },
      },
      rightPriceScale: { borderColor: theme.border },
      timeScale: { borderColor: theme.border },
    })
  }, [theme])

  useEffect(() => {
    chartRef.current?.applyOptions({ height: visibleHeight })
  }, [visibleHeight])

  useEffect(() => {
    ema20Ref.current?.applyOptions({ visible: showEma20 })
    ema60Ref.current?.applyOptions({ visible: showEma60 })
  }, [showEma20, showEma60])

  useEffect(() => {
    const chart = chartRef.current
    const candle = candleRef.current
    const volume = volumeRef.current
    const ema20Series = ema20Ref.current
    const ema60Series = ema60Ref.current
    if (!chart || !candle || !volume || !ema20Series || !ema60Series) return

    const context = `${market}:${symbol}:${interval}`
    const contextChanged = lastContextRef.current !== context
    const visibleRange = contextChanged ? null : chart.timeScale().getVisibleRange()

    candle.setData(validBars.map(bar => ({
      time: bar.time as UTCTimestamp,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    })))
    volume.setData(validBars.map(bar => ({
      time: bar.time as UTCTimestamp,
      value: bar.volume,
      color: bar.close >= bar.open ? 'rgba(34,197,94,0.38)' : 'rgba(239,68,68,0.38)',
    })))
    ema20Series.setData(ema20.map(point => ({ time: point.time as UTCTimestamp, value: point.value })))
    ema60Series.setData(ema60.map(point => ({ time: point.time as UTCTimestamp, value: point.value })))
    candle.setMarkers(markers.map(marker => ({ ...marker, time: marker.time as UTCTimestamp })) as SeriesMarker<Time>[])

    for (const line of priceLinesRef.current) candle.removePriceLine(line)
    priceLinesRef.current = []
    if (validBars.length > 0) {
      priceLinesRef.current = levels
        .filter(level => Number.isFinite(level.price) && level.price > 0 && level.label.trim() !== '' && level.color.trim() !== '')
        .map(level => candle.createPriceLine({
          price: level.price,
          color: level.color,
          title: level.label,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          lineVisible: true,
        }))
    }

    if (contextChanged) {
      chart.timeScale().fitContent()
    } else if (visibleRange && validBars.length > 0) {
      const firstTime = validBars[0].time
      const lastTime = validBars[validBars.length - 1].time
      const from = typeof visibleRange.from === 'number' ? visibleRange.from : null
      const to = typeof visibleRange.to === 'number' ? visibleRange.to : null
      if (from !== null && to !== null && from <= lastTime && to >= firstTime) {
        chart.timeScale().setVisibleRange(visibleRange)
      } else {
        chart.timeScale().fitContent()
      }
    } else {
      chart.timeScale().fitContent()
    }
    lastContextRef.current = context
  }, [validBars, ema20, ema60, markers, levels, market, symbol, interval])

  const empty = validBars.length === 0

  return <section className="space-y-2">
    <div className="flex flex-wrap items-center gap-3 text-xs text-secondary">
      <span className="font-medium text-foreground">{symbol} · {interval}</span>
      <label className="flex cursor-pointer items-center gap-1.5">
        <input type="checkbox" checked={showEma20} onChange={event => setShowEma20(event.target.checked)} />
        <span className="text-amber-400">EMA20</span>
      </label>
      <label className="flex cursor-pointer items-center gap-1.5">
        <input type="checkbox" checked={showEma60} onChange={event => setShowEma60(event.target.checked)} />
        <span className="text-sky-400">EMA60</span>
      </label>
      <button
        type="button"
        onClick={() => chartRef.current?.timeScale().fitContent()}
        className="ml-auto rounded-btn border border-border px-2.5 py-1.5 text-secondary hover:text-foreground"
      >
        适配视图
      </button>
    </div>
    <div className="relative w-full">
      <div ref={containerRef} className="w-full" style={{ height: visibleHeight }} />
      {empty && <div role="status" className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-muted">
        暂无有效 K 线数据
      </div>}
    </div>
    <p className="text-[10px] leading-4 text-muted">
      NOTICE：TradingView Lightweight Charts™ · Copyright (с) 2024 TradingView, Inc. · 数据仅显示已收盘 K 线与对应的模拟成交记录；
      <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer" className="ml-1 underline underline-offset-2">https://www.tradingview.com/</a>
    </p>
  </section>
}
