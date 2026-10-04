import { useEffect, useId, useMemo, useRef, useState } from 'react'
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type AutoscaleInfo,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type MouseEventParams,
  type Time,
} from 'lightweight-charts'
import { chartTheme, useTheme } from '@/lib/theme'
import type { ChartMarker, ChartPriceLine } from '@/components/EChartsCandlestick'
import {
  MOVING_AVERAGE_KEYS,
  dailyChartDateFromTime,
  formatTradingViewPriceSummary,
  normalizeTradingViewDailyRows,
  resolveDailyViewport,
  toTradingViewCandles,
  toTradingViewMarkers,
  toTradingViewMovingAverage,
  toTradingViewVolume,
  type DailyKlineInput,
  type LogicalViewport,
  type MovingAverageKey,
  type TradingViewDailyBar,
} from './tradingview-daily'

const UP = '#C74040'
const DOWN = '#2D9B65'
const MOVING_AVERAGE_COLORS: Record<MovingAverageKey, string> = {
  ma5: '#A1A1AA',
  ma10: '#3B82F6',
  ma20: '#F97316',
  ma60: '#8B5CF6',
}
const MOVING_AVERAGE_LABELS: Record<MovingAverageKey, string> = {
  ma5: 'MA5',
  ma10: 'MA10',
  ma20: 'MA20',
  ma60: 'MA60',
}

export interface TradingViewDailyChartProps {
  data: readonly DailyKlineInput[]
  symbol: string
  height?: number
  showMA?: boolean
  showControls?: boolean
  visibleBars?: number | 'all'
  contextKey: string
  markers?: ChartMarker[]
  priceLines?: ChartPriceLine[]
  linkedPrice?: number | null
  onDateClick?: (date: string) => void
  onPriceDoubleClick?: (price: number, currentPrice: number) => void
}

export function TradingViewDailyChart({
  data,
  symbol,
  height = 480,
  showMA = true,
  showControls = true,
  visibleBars = 60,
  contextKey,
  markers,
  priceLines,
  linkedPrice,
  onDateClick,
  onPriceDoubleClick,
}: TradingViewDailyChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const movingAverageRefs = useRef<Partial<Record<MovingAverageKey, ISeriesApi<'Line'>>>>({})
  const priceLineRefs = useRef<IPriceLine[]>([])
  const scalePriceLinesRef = useRef<number[]>([])
  const previousContextRef = useRef<string | null>(null)
  const previousCountRef = useRef(0)
  const heightRef = useRef(height)
  const callbacksRef = useRef({ onDateClick, onPriceDoubleClick })
  const barsByDateRef = useRef(new Map<string, TradingViewDailyBar>())
  const cleanupWindowResizeRef = useRef<(() => void) | null>(null)
  const theme = chartTheme(useTheme())
  const [showVolume, setShowVolume] = useState(true)
  const [visibleMovingAverages, setVisibleMovingAverages] = useState<Record<MovingAverageKey, boolean>>({
    ma5: showMA,
    ma10: showMA,
    ma20: showMA,
    ma60: showMA,
  })
  const [hoveredDate, setHoveredDate] = useState<string | null>(null)
  const descriptionId = useId()
  const themeRef = useRef(theme)
  const visibleMovingAveragesRef = useRef(visibleMovingAverages)

  const validated = useMemo(() => normalizeTradingViewDailyRows(data), [data])
  const barsByDate = useMemo(
    () => new Map(validated.bars.map(bar => [bar.date, bar])),
    [validated.bars],
  )
  const availableDates = useMemo(() => new Set(barsByDate.keys()), [barsByDate])
  const seriesMarkers = useMemo(
    () => toTradingViewMarkers(markers, availableDates),
    [markers, availableDates],
  )
  const currentBar = (hoveredDate ? barsByDate.get(hoveredDate) : undefined)
    ?? validated.bars.at(-1)

  heightRef.current = height
  themeRef.current = theme
  visibleMovingAveragesRef.current = visibleMovingAverages
  callbacksRef.current = { onDateClick, onPriceDoubleClick }
  barsByDateRef.current = barsByDate

  useEffect(() => {
    const element = containerRef.current
    if (!element) return
    const initialTheme = themeRef.current

    const chart = createChart(element, {
      width: Math.max(1, element.clientWidth),
      height: heightRef.current,
      layout: {
        attributionLogo: true,
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: initialTheme.text,
        fontFamily: 'JetBrains Mono, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: initialTheme.grid },
        horzLines: { color: initialTheme.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: initialTheme.crosshair, labelBackgroundColor: initialTheme.crosshairLabelBg },
        horzLine: { color: initialTheme.crosshair, labelBackgroundColor: initialTheme.crosshairLabelBg },
      },
      rightPriceScale: {
        borderColor: initialTheme.border,
        scaleMargins: { top: 0.06, bottom: 0.23 },
      },
      timeScale: {
        borderColor: initialTheme.border,
        timeVisible: false,
        secondsVisible: false,
      },
      localization: {
        dateFormat: 'yyyy-MM-dd',
        priceFormatter: (price: number) => price.toFixed(2),
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
      autoscaleInfoProvider: (base: () => AutoscaleInfo | null) => {
        const info = base()
        const values = scalePriceLinesRef.current
        if (values.length === 0) return info
        if (info === null) {
          return {
            priceRange: {
              minValue: Math.min(...values),
              maxValue: Math.max(...values),
            },
          }
        }
        return {
          ...info,
          priceRange: {
            minValue: Math.min(info.priceRange.minValue, ...values),
            maxValue: Math.max(info.priceRange.maxValue, ...values),
          },
        }
      },
    })
    const volume = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      lastValueVisible: false,
      priceLineVisible: false,
    })
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })

    const averages: Partial<Record<MovingAverageKey, ISeriesApi<'Line'>>> = {}
    for (const key of MOVING_AVERAGE_KEYS) {
      averages[key] = chart.addLineSeries({
        color: MOVING_AVERAGE_COLORS[key],
        lineWidth: 1,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        visible: visibleMovingAveragesRef.current[key],
      })
    }

    chartRef.current = chart
    candleRef.current = candle
    volumeRef.current = volume
    movingAverageRefs.current = averages

    const handleClick = (event: MouseEventParams<Time>) => {
      const date = dailyChartDateFromTime(event.time)
      if (date && barsByDateRef.current.has(date)) callbacksRef.current.onDateClick?.(date)
    }
    const handleCrosshairMove = (event: MouseEventParams<Time>) => {
      const date = dailyChartDateFromTime(event.time)
      const nextDate = date && barsByDateRef.current.has(date) ? date : null
      setHoveredDate(previous => previous === nextDate ? previous : nextDate)
    }
    const handleDoubleClick = (event: MouseEventParams<Time>) => {
      if (!event.point || event.point.y < 0 || event.point.y > heightRef.current * 0.78) return
      const price = candle.coordinateToPrice(event.point.y)
      const latest = [...barsByDateRef.current.values()].at(-1)
      if (typeof price === 'number' && Number.isFinite(price) && price > 0 && latest) {
        callbacksRef.current.onPriceDoubleClick?.(price, latest.close)
      }
    }
    chart.subscribeClick(handleClick)
    chart.subscribeCrosshairMove(handleCrosshairMove)
    chart.subscribeDblClick(handleDoubleClick)

    const resize = (width: number) => {
      if (width > 0) chart.applyOptions({ width, height: heightRef.current })
    }
    let observer: ResizeObserver | null = null
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(entries => {
        resize(Math.floor(entries[0]?.contentRect.width ?? element.clientWidth))
      })
      observer.observe(element)
    } else {
      const handleWindowResize = () => resize(element.clientWidth)
      window.addEventListener('resize', handleWindowResize)
      cleanupWindowResizeRef.current = handleWindowResize
    }

    return () => {
      observer?.disconnect()
      if (cleanupWindowResizeRef.current) {
        window.removeEventListener('resize', cleanupWindowResizeRef.current)
        cleanupWindowResizeRef.current = null
      }
      chart.unsubscribeClick(handleClick)
      chart.unsubscribeCrosshairMove(handleCrosshairMove)
      chart.unsubscribeDblClick(handleDoubleClick)
      for (const line of priceLineRefs.current) candle.removePriceLine(line)
      priceLineRefs.current = []
      scalePriceLinesRef.current = []
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volumeRef.current = null
      movingAverageRefs.current = {}
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
    chartRef.current?.applyOptions({ height })
  }, [height])

  useEffect(() => {
    setVisibleMovingAverages({
      ma5: showMA,
      ma10: showMA,
      ma20: showMA,
      ma60: showMA,
    })
  }, [showMA])

  useEffect(() => {
    volumeRef.current?.applyOptions({ visible: showVolume })
  }, [showVolume])

  useEffect(() => {
    for (const key of MOVING_AVERAGE_KEYS) {
      movingAverageRefs.current[key]?.applyOptions({ visible: visibleMovingAverages[key] })
    }
  }, [visibleMovingAverages])

  useEffect(() => {
    const chart = chartRef.current
    const candle = candleRef.current
    if (!chart || !candle) return

    const scaleValues = (priceLines ?? [])
      .filter(line => line.start == null && line.end == null)
      .map(line => line.value)
      .filter(value => typeof value === 'number' && Number.isFinite(value) && value > 0)
    scalePriceLinesRef.current = scaleValues

    for (const line of priceLineRefs.current) candle.removePriceLine(line)
    priceLineRefs.current = []
    if (validated.bars.length > 0) {
      const visiblePriceLines = (priceLines ?? [])
        .filter(line => line.start == null && line.end == null && Number.isFinite(line.value) && line.value > 0)
        .map(line => candle.createPriceLine({
          price: line.value,
          color: line.color ?? '#FACC15',
          title: line.label ?? '',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          lineVisible: true,
        }))
      if (typeof linkedPrice === 'number' && Number.isFinite(linkedPrice) && linkedPrice > 0) {
        visiblePriceLines.push(candle.createPriceLine({
          price: linkedPrice,
          color: '#60A5FA',
          title: '分时关联价格',
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          lineVisible: true,
        }))
      }
      priceLineRefs.current = visiblePriceLines
    }
  }, [linkedPrice, priceLines, validated.bars.length])

  useEffect(() => {
    const chart = chartRef.current
    const candle = candleRef.current
    const volume = volumeRef.current
    if (!chart || !candle || !volume) return
    if (validated.bars.length === 0) {
      candle.setData([])
      volume.setData([])
      for (const key of MOVING_AVERAGE_KEYS) movingAverageRefs.current[key]?.setData([])
      candle.setMarkers([])
      previousContextRef.current = contextKey
      previousCountRef.current = 0
      setHoveredDate(null)
      return
    }

    const previousCount = previousCountRef.current
    const contextChanged = previousContextRef.current !== contextKey
    const previousRange: LogicalViewport | null = chart.timeScale().getVisibleLogicalRange()
    candle.setData(toTradingViewCandles(validated.bars))
    volume.setData(toTradingViewVolume(validated.bars))
    for (const key of MOVING_AVERAGE_KEYS) {
      movingAverageRefs.current[key]?.setData(toTradingViewMovingAverage(validated.bars, key))
    }
    candle.setMarkers(seriesMarkers)

    const viewport = resolveDailyViewport({
      previousRange,
      previousCount,
      nextCount: validated.bars.length,
      contextChanged,
      visibleBars,
    })
    if (viewport.kind === 'fit') chart.timeScale().fitContent()
    else if (viewport.kind === 'range') chart.timeScale().setVisibleLogicalRange(viewport.range)
    previousContextRef.current = contextKey
    previousCountRef.current = validated.bars.length
    setHoveredDate(null)
  }, [contextKey, seriesMarkers, validated.bars, visibleBars])

  const summary = currentBar ? formatTradingViewPriceSummary(currentBar) : '暂无有效日K价格数据'
  const movingAverageSummary = currentBar
    ? MOVING_AVERAGE_KEYS
      .filter(key => visibleMovingAverages[key])
      .map(key => MOVING_AVERAGE_LABELS[key] + ' ' + (currentBar[key]?.toFixed(2) ?? '—'))
      .join('，')
    : ''

  return (
    <section className="space-y-1.5">
      {showControls && <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px] text-secondary">
        <span className="font-medium text-foreground">{symbol} · 日K</span>
        <label className="flex cursor-pointer items-center gap-1.5">
          <input
            type="checkbox"
            checked={showVolume}
            onChange={event => setShowVolume(event.target.checked)}
            aria-label="显示成交量"
          />
          <span>成交量</span>
        </label>
        {MOVING_AVERAGE_KEYS.map(key => (
          <label key={key} className="flex cursor-pointer items-center gap-1.5">
            <input
              type="checkbox"
              checked={visibleMovingAverages[key]}
              onChange={event => setVisibleMovingAverages(previous => ({
                ...previous,
                [key]: event.target.checked,
              }))}
              aria-label={'显示' + MOVING_AVERAGE_LABELS[key]}
            />
            <span style={{ color: MOVING_AVERAGE_COLORS[key] }}>{MOVING_AVERAGE_LABELS[key]}</span>
          </label>
        ))}
        <button
          type="button"
          onClick={() => chartRef.current?.timeScale().fitContent()}
          className="ml-auto rounded-btn border border-border px-2.5 py-1 text-secondary hover:text-foreground"
        >
          适配视图
        </button>
      </div>}

      <div className="relative w-full">
        <div
          ref={containerRef}
          className="w-full"
          style={{ height }}
          role="img"
          aria-label={symbol + ' TradingView 日K图，十字线查看单日价格与成交量'}
          aria-describedby={descriptionId}
        />
        {validated.bars.length === 0 && (
          <div role="status" className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-muted">
            暂无有效 K 线数据
          </div>
        )}
      </div>

      <p id={descriptionId} className="sr-only">
        蜡烛颜色使用A股习惯，红色表示收盘价不低于开盘价，绿色表示收盘价低于开盘价。价格保留两位小数；无效OHLC不绘制，缺失或无效成交量不绘制对应成交量柱。可用鼠标十字线查看日期和OHLCV。
      </p>
      <div role="status" aria-live="polite" aria-atomic="true" className="min-h-4 text-[10px] leading-4 text-secondary">
        {summary}{movingAverageSummary ? '；' + movingAverageSummary : ''}
      </div>

      {validated.invalidRows > 0 && (
        <p role="status" className="text-[10px] leading-4 text-muted">
          已忽略 {validated.invalidRows} 条日期或OHLC无效的K线。
        </p>
      )}
      {validated.conflictingDates > 0 && (
        <p role="status" className="text-[10px] leading-4 text-muted">
          {validated.conflictingDates} 个日期存在互相冲突的重复K线，已隐藏这些日期。
        </p>
      )}
      {validated.missingVolumeBars > 0 && (
        <p role="status" className="text-[10px] leading-4 text-muted">
          {validated.missingVolumeBars} 根有效价格K线缺少有效成交量；保留蜡烛，仅省略对应成交量柱。
        </p>
      )}

      <p className="text-[10px] leading-4 text-muted">
        NOTICE：TradingView Lightweight Charts™ · Copyright (с) 2024 TradingView, Inc. ·
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer" className="ml-1 underline underline-offset-2">
          TradingView
        </a>
      </p>
    </section>
  )
}
