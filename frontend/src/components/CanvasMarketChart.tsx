import { useEffect, useMemo, useRef, useState, type MouseEvent, type PointerEvent } from 'react'
import { chartTheme, useTheme } from '@/lib/theme'
import {
  canvasIndexAtX,
  defaultCanvasViewport,
  panCanvasViewport,
  resolveCanvasPriceRange,
  resolveCanvasViewport,
  zoomCanvasViewport,
  type CanvasViewport,
  type CanvasViewportMemory,
} from '@/lib/canvas-chart/math'

export type CanvasChartTime = string | number

export interface CanvasChartBar {
  time: CanvasChartTime
  open: number
  high: number
  low: number
  close: number
  volume: number | null
}

export interface CanvasChartSeries {
  id: string
  label: string
  color: string
  values: readonly (number | null | undefined)[]
  visibleByDefault?: boolean
}

export interface CanvasChartMarker {
  time: CanvasChartTime
  position: 'above' | 'below'
  shape?: 'arrow' | 'circle' | 'diamond'
  color: string
  label?: string
}

export interface CanvasChartPriceLine {
  value: number
  label?: string
  color?: string
  start?: CanvasChartTime
  end?: CanvasChartTime
  dash?: 'dashed' | 'dotted' | 'solid'
}

export interface CanvasMarketChartProps {
  bars: readonly CanvasChartBar[]
  symbol: string
  title?: string
  contextKey: string
  viewportMemory?: CanvasViewportMemory
  height?: number
  visibleBars?: number | 'all'
  showControls?: boolean
  series?: readonly CanvasChartSeries[]
  markers?: readonly CanvasChartMarker[]
  priceLines?: readonly CanvasChartPriceLine[]
  linkedPrice?: number | null
  upColor?: string
  downColor?: string
  formatPrice?: (value: number | null | undefined) => string
  formatTime?: (time: CanvasChartTime, includeYear?: boolean) => string
  extraSummary?: (barIndex: number) => string | null
  onBarClick?: (time: CanvasChartTime) => void
  onPriceDoubleClick?: (price: number, currentPrice: number) => void
}

interface ChartDimensions {
  width: number
  height: number
  pixelRatio: number
}

interface HoverPoint {
  index: number
  y: number
}

const EMPTY_SERIES: readonly CanvasChartSeries[] = []
const EMPTY_MARKERS: readonly CanvasChartMarker[] = []
const EMPTY_PRICE_LINES: readonly CanvasChartPriceLine[] = []
const SINGLE_CLICK_DELAY_MS = 500

function safeHeight(value: number | undefined): number {
  return Number.isFinite(value) && (value ?? 0) > 0 ? Math.round(value!) : 400
}

function defaultFormatPrice(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
    : '—'
}

function defaultFormatTime(time: CanvasChartTime, includeYear = false): string {
  if (typeof time === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(time)) return time
  const date = new Date(typeof time === 'number' ? time * 1_000 : time)
  if (!Number.isFinite(date.getTime())) return String(time)
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: includeYear ? 'numeric' : undefined,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date)
}

function timeKey(time: CanvasChartTime): string {
  return `${typeof time}:${String(time)}`
}

function compareTime(left: CanvasChartTime, right: CanvasChartTime): number {
  if (typeof left === 'number' && typeof right === 'number') return left - right
  return String(left).localeCompare(String(right))
}

function resolveTimeBoundary(
  bars: readonly CanvasChartBar[],
  target: CanvasChartTime,
  side: 'start' | 'end',
): number {
  const exact = bars.findIndex(bar => timeKey(bar.time) === timeKey(target))
  if (exact >= 0) return exact
  let low = 0
  let high = bars.length
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    const comparison = compareTime(bars[middle].time, target)
    if (comparison < 0 || (side === 'end' && comparison === 0)) low = middle + 1
    else high = middle
  }
  return side === 'start' ? low : low - 1
}

interface VisiblePriceLine {
  line: CanvasChartPriceLine
  firstIndex: number
  lastIndex: number
}

function resolveVisiblePriceLines(
  bars: readonly CanvasChartBar[],
  start: number,
  end: number,
  priceLines: readonly CanvasChartPriceLine[],
): VisiblePriceLine[] {
  if (bars.length === 0 || end < start) return []
  return priceLines.flatMap(line => {
    if (!Number.isFinite(line.value) || line.value <= 0) return []
    const firstIndex = line.start == null ? 0 : resolveTimeBoundary(bars, line.start, 'start')
    const lastIndex = line.end == null ? bars.length - 1 : resolveTimeBoundary(bars, line.end, 'end')
    return firstIndex <= end && lastIndex >= start && firstIndex <= lastIndex
      ? [{ line, firstIndex, lastIndex }]
      : []
  })
}

function rgba(color: string, alpha: number): string {
  const match = color.trim().match(/^#([\da-f]{3}|[\da-f]{6})$/i)
  if (!match) return color
  const hex = match[1].length === 3
    ? match[1].split('').map(part => part + part).join('')
    : match[1]
  const red = Number.parseInt(hex.slice(0, 2), 16)
  const green = Number.parseInt(hex.slice(2, 4), 16)
  const blue = Number.parseInt(hex.slice(4, 6), 16)
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`
}

function setDash(ctx: CanvasRenderingContext2D, style: CanvasChartPriceLine['dash']): void {
  if (style === 'dotted') ctx.setLineDash([2, 3])
  else if (style === 'dashed') ctx.setLineDash([5, 4])
  else ctx.setLineDash([])
}

export function CanvasMarketChart({
  bars,
  symbol,
  title = '日K',
  contextKey,
  viewportMemory,
  height = 480,
  visibleBars = 60,
  showControls = true,
  series = EMPTY_SERIES,
  markers = EMPTY_MARKERS,
  priceLines = EMPTY_PRICE_LINES,
  linkedPrice,
  upColor = '#C74040',
  downColor = '#2D9B65',
  formatPrice = defaultFormatPrice,
  formatTime = defaultFormatTime,
  extraSummary,
  onBarClick,
  onPriceDoubleClick,
}: CanvasMarketChartProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const viewportRef = useRef<CanvasViewport>({ endIndex: -1, visibleCount: 0 })
  const previousCountRef = useRef(0)
  const previousTimesRef = useRef<readonly CanvasChartTime[]>([])
  const previousContextRef = useRef<string | null>(null)
  const dragRef = useRef<{ startX: number; startViewport: CanvasViewport; moved: boolean } | null>(null)
  const pendingClickRef = useRef<{ time: CanvasChartTime; timer: number } | null>(null)
  const suppressNextClickRef = useRef(false)
  const plotRightRef = useRef(574)
  const wheelHandlerRef = useRef<(event: globalThis.WheelEvent) => void>(() => {})
  const seriesDefaultsRef = useRef(new Map<string, boolean>(series.map(item =>
    [item.id, item.visibleByDefault ?? true] as const)))
  const [dimensions, setDimensions] = useState<ChartDimensions>({ width: 640, height: safeHeight(height), pixelRatio: 1 })
  const [hover, setHover] = useState<HoverPoint | null>(null)
  const [showVolume, setShowVolume] = useState(true)
  const [visibleSeries, setVisibleSeries] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(series.map(item => [item.id, item.visibleByDefault ?? true])),
  )
  const [viewportRevision, setViewportRevision] = useState(0)
  const theme = chartTheme(useTheme())
  const chartHeight = safeHeight(height)
  const priceFormatter = formatPrice

  useEffect(() => {
    const previousDefaults = seriesDefaultsRef.current
    const nextDefaults = new Map<string, boolean>(series.map(item =>
      [item.id, item.visibleByDefault ?? true] as const))
    setVisibleSeries(previous => {
      const next: Record<string, boolean> = {}
      for (const item of series) {
        const nextDefault = item.visibleByDefault ?? true
        const defaultChanged = previousDefaults.has(item.id) && previousDefaults.get(item.id) !== nextDefault
        next[item.id] = defaultChanged ? nextDefault : previous[item.id] ?? nextDefault
      }
      if (Object.keys(previous).length === Object.keys(next).length
        && Object.entries(next).every(([key, value]) => previous[key] === value)) return previous
      return next
    })
    seriesDefaultsRef.current = nextDefaults
  }, [series])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const resize = (width: number) => {
      const nextWidth = width > 0 ? Math.floor(width) : 640
      const pixelRatio = Math.max(1, Math.min(4, window.devicePixelRatio || 1))
      setDimensions(previous => previous.width === nextWidth && previous.height === chartHeight
        && previous.pixelRatio === pixelRatio
        ? previous
        : { width: nextWidth, height: chartHeight, pixelRatio })
    }
    let ratioQuery: MediaQueryList | null = null
    function watchPixelRatio() {
      ratioQuery?.removeEventListener('change', onPixelRatioChange)
      if (typeof window.matchMedia !== 'function') return
      ratioQuery = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`)
      ratioQuery.addEventListener('change', onPixelRatioChange, { once: true })
    }
    const onPixelRatioChange = () => {
      resize(host.clientWidth)
      watchPixelRatio()
    }
    const onWindowResize = () => {
      resize(host.clientWidth)
      watchPixelRatio()
    }
    resize(host.clientWidth)
    window.addEventListener('resize', onWindowResize)
    watchPixelRatio()
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(entries => resize(entries[0]?.contentRect.width ?? host.clientWidth))
      observer.observe(host)
      return () => {
        observer.disconnect()
        window.removeEventListener('resize', onWindowResize)
        ratioQuery?.removeEventListener('change', onPixelRatioChange)
      }
    }
    return () => {
      window.removeEventListener('resize', onWindowResize)
      ratioQuery?.removeEventListener('change', onPixelRatioChange)
    }
  }, [chartHeight])

  useEffect(() => {
    const contextChanged = previousContextRef.current !== contextKey
    const nextTimes = bars.map(bar => bar.time)
    viewportRef.current = resolveCanvasViewport({
      previous: viewportRef.current.visibleCount > 0 ? viewportRef.current : null,
      previousCount: previousCountRef.current,
      nextCount: bars.length,
      previousTimes: previousTimesRef.current,
      nextTimes,
      contextChanged,
      visibleBars,
    })
    const saved = contextChanged || previousCountRef.current === 0 ? viewportMemory?.read(contextKey) : null
    if (saved && bars.length && Number.isFinite(saved.visibleCount) && saved.visibleCount > 0) {
      const visibleCount = Math.min(bars.length, Math.max(1, Math.floor(saved.visibleCount)))
      const end = saved.atLatest ? bars.length - 1 : resolveTimeBoundary(bars, saved.endTime, 'end')
      viewportRef.current = { visibleCount, endIndex: Math.min(bars.length - 1, Math.max(visibleCount - 1, end)) }
    }
    if (contextChanged && pendingClickRef.current) {
      window.clearTimeout(pendingClickRef.current.timer)
      pendingClickRef.current = null
    }
    previousContextRef.current = contextKey
    previousCountRef.current = bars.length
    previousTimesRef.current = nextTimes
    setHover(null)
  }, [bars, contextKey, visibleBars, viewportMemory])

  useEffect(() => () => {
    if (pendingClickRef.current) window.clearTimeout(pendingClickRef.current.timer)
  }, [])

  const timeIndex = useMemo(() => new Map(bars.map((bar, index) => [timeKey(bar.time), index])), [bars])
  const currentIndex = hover?.index ?? bars.length - 1
  const currentBar = currentIndex >= 0 ? bars[currentIndex] : undefined
  const summary = currentBar
    ? [
      formatTime(currentBar.time, true),
      `开 ${priceFormatter(currentBar.open)}`,
      `高 ${priceFormatter(currentBar.high)}`,
      `低 ${priceFormatter(currentBar.low)}`,
      `收 ${priceFormatter(currentBar.close)}`,
      currentBar.volume == null ? '成交量缺失' : `量 ${currentBar.volume.toLocaleString('zh-CN')}`,
      ...series.filter(item => visibleSeries[item.id])
        .map(item => `${item.label} ${priceFormatter(item.values[currentIndex])}`),
      extraSummary?.(currentIndex) ?? '',
    ].filter(Boolean).join('　')
    : '暂无有效 K 线价格数据'

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const { width, height: chartH, pixelRatio } = dimensions
    canvas.width = Math.max(1, Math.round(width * pixelRatio))
    canvas.height = Math.max(1, Math.round(chartH * pixelRatio))
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0)
    ctx.clearRect(0, 0, width, chartH)
    ctx.font = '11px JetBrains Mono, ui-monospace, monospace'
    ctx.lineWidth = 1
    ctx.textBaseline = 'middle'

    const left = 10
    let right = Math.max(left + 1, width - 66)
    plotRightRef.current = right
    const top = 16
    const volumeBottom = chartH - 22
    const priceBottom = showVolume ? Math.max(top + 1, Math.round(chartH * 0.70)) : chartH - 26
    const volumeTop = priceBottom + 13
    const plotCount = Math.max(1, viewportRef.current.visibleCount)
    const start = Math.max(0, viewportRef.current.endIndex - plotCount + 1)
    const end = Math.min(bars.length - 1, viewportRef.current.endIndex)
    ctx.fillStyle = theme.text
    ctx.strokeStyle = theme.grid
    ctx.fillText(symbol + ' · ' + title, left, 8)
    const visibleIndices = bars.length > 0 ? { start, end } : null
    if (bars.length === 0 || !visibleIndices) {
      for (let step = 0; step <= 4; step += 1) {
        const y = top + ((priceBottom - top) * step) / 4
        ctx.beginPath()
        ctx.moveTo(left, y)
        ctx.lineTo(right, y)
        ctx.stroke()
      }
      return
    }

    const displayedSeries = series.filter(item => visibleSeries[item.id])
    const extraValues: (number | null)[] = []
    for (const item of displayedSeries) {
      for (let index = start; index <= end; index += 1) extraValues.push(item.values[index] ?? null)
    }
    const visiblePriceLines = resolveVisiblePriceLines(bars, start, end, priceLines)
    for (const { line } of visiblePriceLines) extraValues.push(line.value)
    if (typeof linkedPrice === 'number' && Number.isFinite(linkedPrice) && linkedPrice > 0) extraValues.push(linkedPrice)
    const priceRange = resolveCanvasPriceRange(bars, viewportRef.current, extraValues)
    const priceSpan = Math.max(Number.EPSILON, priceRange.high - priceRange.low)
    const axisTickLabels = Array.from({ length: 5 }, (_, step) =>
      priceFormatter(priceRange.high - (priceSpan * step) / 4))
    const axisLineLabels = visiblePriceLines.filter(({ line }) => line.label != null)
      .map(({ line }) => line.label ?? '')
    const axisLabels = [...axisTickLabels, ...axisLineLabels,
      ...(typeof linkedPrice === 'number' && Number.isFinite(linkedPrice) && linkedPrice > 0 ? ['分时关联价格'] : [])]
    const widestAxisLabel = axisLabels.reduce((widest, label) => Math.max(widest, ctx.measureText(label).width), 0)
    const axisWidth = Math.max(66, Math.ceil(widestAxisLabel) + 12)
    right = Math.max(left + 1, width - Math.min(axisWidth, Math.max(1, width - left - 1)))
    plotRightRef.current = right
    const slotWidth = (right - left) / Math.max(1, end - start + 1)
    const yFor = (value: number) => priceBottom - ((value - priceRange.low) / priceSpan) * (priceBottom - top)
    const xFor = (index: number) => left + (index - start + 0.5) * slotWidth

    for (let step = 0; step <= 4; step += 1) {
      const value = priceRange.high - (priceSpan * step) / 4
      const y = yFor(value)
      ctx.strokeStyle = theme.grid
      ctx.beginPath()
      ctx.moveTo(left, y)
      ctx.lineTo(right, y)
      ctx.stroke()
      ctx.fillStyle = theme.text
      ctx.textAlign = 'left'
      ctx.fillText(priceFormatter(value), right + 5, y)
    }
    const drawnTimeIndices = new Set<number>()
    for (let step = 0; step <= 6; step += 1) {
      const relativeIndex = Math.min(end - start, Math.round(((end - start) * step) / 6))
      const index = start + relativeIndex
      const x = xFor(index)
      ctx.strokeStyle = theme.grid
      ctx.beginPath()
      ctx.moveTo(x, top)
      ctx.lineTo(x, showVolume ? volumeBottom : priceBottom)
      ctx.stroke()
      const showTimeLabel = step === 6 || (step === 0 && right - left >= 180)
        || (right - left >= 480 && step % 2 === 0)
      if (showTimeLabel && !drawnTimeIndices.has(index)) {
        drawnTimeIndices.add(index)
        ctx.fillStyle = theme.text
        ctx.textAlign = step === 0 ? 'left' : step === 6 ? 'right' : 'center'
        ctx.fillText(formatTime(bars[index].time), x, chartH - 9)
      }
    }

    const maxVolume = bars.slice(start, end + 1).reduce((maximum, bar) =>
      bar.volume != null && Number.isFinite(bar.volume) && bar.volume >= 0
        ? Math.max(maximum, bar.volume)
        : maximum, 0)
    if (showVolume) {
      ctx.strokeStyle = theme.border
      ctx.beginPath()
      ctx.moveTo(left, volumeTop)
      ctx.lineTo(right, volumeTop)
      ctx.stroke()
      if (maxVolume > 0) {
        for (let index = start; index <= end; index += 1) {
          const volume = bars[index].volume
          if (volume == null || !Number.isFinite(volume) || volume < 0) continue
          const x = xFor(index)
          const barHeight = (volume / maxVolume) * Math.max(1, volumeBottom - volumeTop)
          ctx.fillStyle = rgba(bars[index].close >= bars[index].open ? upColor : downColor, 0.48)
          ctx.fillRect(x - Math.max(1, Math.min(slotWidth * 0.7, 18)) / 2, volumeBottom - barHeight,
            Math.max(1, Math.min(slotWidth * 0.7, 18)), barHeight)
        }
      }
    }

    for (let index = start; index <= end; index += 1) {
      const bar = bars[index]
      const x = xFor(index)
      const rising = bar.close >= bar.open
      const color = rising ? upColor : downColor
      const candleWidth = Math.max(1, Math.min(slotWidth * 0.7, 18))
      ctx.strokeStyle = color
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.moveTo(x, yFor(bar.high))
      ctx.lineTo(x, yFor(bar.low))
      ctx.stroke()
      const yOpen = yFor(bar.open)
      const yClose = yFor(bar.close)
      const bodyHeight = Math.max(1, Math.abs(yClose - yOpen))
      ctx.fillRect(x - candleWidth / 2, Math.min(yOpen, yClose), candleWidth, bodyHeight)
    }

    for (const item of displayedSeries) {
      ctx.strokeStyle = item.color
      ctx.lineWidth = 1.35
      ctx.beginPath()
      let drawing = false
      for (let index = start; index <= end; index += 1) {
        const value = item.values[index]
        if (typeof value !== 'number' || !Number.isFinite(value)) {
          drawing = false
          continue
        }
        const x = xFor(index)
        const y = yFor(value)
        if (drawing) ctx.lineTo(x, y)
        else { ctx.moveTo(x, y); drawing = true }
      }
      ctx.stroke()
      ctx.lineWidth = 1
    }

    const drawPriceLine = (value: number, color: string, label: string, lineDash: CanvasChartPriceLine['dash'],
      firstIndex: number, lastIndex: number) => {
      if (!Number.isFinite(value) || value <= 0) return
      const from = Math.max(start, firstIndex)
      const to = Math.min(end, lastIndex)
      if (to < from) return
      const x1 = firstIndex <= start && lastIndex >= end ? left : xFor(from)
      const x2 = firstIndex <= start && lastIndex >= end ? right : xFor(to)
      const y = yFor(value)
      ctx.save()
      ctx.strokeStyle = color
      setDash(ctx, lineDash)
      ctx.beginPath()
      ctx.moveTo(x1, y)
      ctx.lineTo(x2, y)
      ctx.stroke()
      ctx.restore()
      if (label) {
        ctx.fillStyle = color
        ctx.textAlign = 'left'
        ctx.fillText(label, right + 5, y + 11)
      }
    }
    for (const { line, firstIndex, lastIndex } of visiblePriceLines) {
      drawPriceLine(line.value, line.color ?? '#FACC15', line.label ?? '', line.dash ?? 'dashed', firstIndex, lastIndex)
    }
    if (typeof linkedPrice === 'number' && Number.isFinite(linkedPrice) && linkedPrice > 0) {
      drawPriceLine(linkedPrice, '#60A5FA', '分时关联价格', 'dotted', 0, bars.length - 1)
    }

    const markerOffsets = new Map<string, number>()
    for (const marker of markers) {
      const index = timeIndex.get(timeKey(marker.time))
      if (index == null || index < start || index > end) continue
      const key = `${index}:${marker.position}`
      const offset = markerOffsets.get(key) ?? 0
      markerOffsets.set(key, offset + 1)
      const x = xFor(index)
      const bar = bars[index]
      const y = marker.position === 'above'
        ? Math.max(top + 4, yFor(bar.high) - 11 - offset * 13)
        : Math.min(priceBottom - 4, yFor(bar.low) + 11 + offset * 13)
      ctx.fillStyle = marker.color
      ctx.strokeStyle = marker.color
      ctx.beginPath()
      if (marker.shape === 'diamond') {
        ctx.moveTo(x, y - 4); ctx.lineTo(x + 4, y); ctx.lineTo(x, y + 4); ctx.lineTo(x - 4, y); ctx.closePath()
      } else if (marker.shape === 'circle') {
        ctx.arc(x, y, 3.5, 0, Math.PI * 2)
      } else if (marker.position === 'above') {
        ctx.moveTo(x, y - 4); ctx.lineTo(x - 4, y + 3); ctx.lineTo(x + 4, y + 3); ctx.closePath()
      } else {
        ctx.moveTo(x, y + 4); ctx.lineTo(x - 4, y - 3); ctx.lineTo(x + 4, y - 3); ctx.closePath()
      }
      ctx.fill()
      if (marker.label) {
        ctx.font = '10px sans-serif'
        ctx.textAlign = 'center'
        ctx.fillText(marker.label, x, marker.position === 'above' ? y - 9 : y + 10)
        ctx.font = '11px JetBrains Mono, ui-monospace, monospace'
      }
    }

    if (hover && hover.index >= start && hover.index <= end) {
      const x = xFor(hover.index)
      ctx.save()
      ctx.strokeStyle = theme.crosshair
      ctx.setLineDash([3, 3])
      ctx.beginPath()
      ctx.moveTo(x, top)
      ctx.lineTo(x, showVolume ? volumeBottom : priceBottom)
      if (hover.y >= top && hover.y <= priceBottom) {
        ctx.moveTo(left, hover.y)
        ctx.lineTo(right, hover.y)
      }
      ctx.stroke()
      ctx.setLineDash([])
      const price = hover.y >= top && hover.y <= priceBottom
        ? priceRange.high - ((hover.y - top) / Math.max(1, priceBottom - top)) * priceSpan
        : bars[hover.index].close
      const priceText = priceFormatter(price)
      ctx.fillStyle = theme.crosshairLabelBg
      ctx.fillRect(right + 1, Math.max(top, Math.min(priceBottom - 18, hover.y - 9)), 62, 18)
      ctx.fillStyle = theme.tooltipText
      ctx.textAlign = 'left'
      ctx.fillText(priceText, right + 5, Math.max(top + 9, Math.min(priceBottom - 9, hover.y)))
      const dateText = formatTime(bars[hover.index].time)
      const dateWidth = Math.min(width - 4, ctx.measureText(dateText).width + 10)
      const dateX = Math.min(Math.max(left, x - dateWidth / 2), width - dateWidth - 2)
      ctx.fillStyle = theme.crosshairLabelBg
      ctx.fillRect(dateX, chartH - 19, dateWidth, 18)
      ctx.fillStyle = theme.tooltipText
      ctx.textAlign = 'center'
      ctx.fillText(dateText, dateX + dateWidth / 2, chartH - 10)
      ctx.restore()
    }
    ctx.textAlign = 'left'
    ctx.textBaseline = 'alphabetic'
  }, [bars, dimensions, downColor, formatTime, linkedPrice, markers, priceFormatter, priceLines, series,
    showVolume, symbol, theme, timeIndex, title, visibleSeries, viewportRevision, hover])

  const pointerPosition = (event: { currentTarget: HTMLCanvasElement; clientX: number; clientY: number }) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const x = event.clientX - rect.left
    const y = event.clientY - rect.top
    const { width, height: chartH } = dimensions
    const plotLeft = 10
    const plotRight = Math.max(plotLeft + 1, Math.min(width - 1, plotRightRef.current))
    const plotTop = 16
    const priceBottom = showVolume ? Math.max(plotTop + 1, Math.round(chartH * 0.70)) : chartH - 26
    return { x, y, plotLeft, plotRight, plotTop, priceBottom }
  }

  const rememberViewport = () => {
    const view = viewportRef.current
    if (!viewportMemory || !bars.length || !bars[view.endIndex]) return
    viewportMemory.write(contextKey, { endTime: bars[view.endIndex].time,
      visibleCount: view.visibleCount, atLatest: view.endIndex === bars.length - 1 })
  }

  const fitContent = () => {
    viewportRef.current = defaultCanvasViewport(bars.length, 'all')
    rememberViewport()
    setHover(null)
    setViewportRevision(value => value + 1)
  }

  const clearPendingClick = () => {
    if (!pendingClickRef.current) return
    window.clearTimeout(pendingClickRef.current.timer)
    pendingClickRef.current = null
  }

  const handlePointerDown = (event: PointerEvent<HTMLCanvasElement>) => {
    if (event.button !== 0) return
    suppressNextClickRef.current = false
    if (bars.length === 0) return
    const point = pointerPosition(event)
    if (point.x < point.plotLeft || point.x > point.plotRight) return
    event.currentTarget.setPointerCapture?.(event.pointerId)
    dragRef.current = { startX: point.x, startViewport: { ...viewportRef.current }, moved: false }
  }

  const handlePointerMove = (event: PointerEvent<HTMLCanvasElement>) => {
    if (bars.length === 0) return
    const point = pointerPosition(event)
    const drag = dragRef.current
    if (drag) {
      const deltaX = point.x - drag.startX
      if (Math.abs(deltaX) > 3) drag.moved = true
      if (drag.moved) {
        const count = Math.max(1, drag.startViewport.visibleCount)
        const slotWidth = (point.plotRight - point.plotLeft) / count
        viewportRef.current = panCanvasViewport(drag.startViewport, deltaX / Math.max(1, slotWidth), bars.length)
        setHover(null)
        setViewportRevision(value => value + 1)
        return
      }
    }
    const relative = canvasIndexAtX({
      x: point.x,
      plotLeft: point.plotLeft,
      plotRight: point.plotRight,
      startIndex: Math.max(0, viewportRef.current.endIndex - viewportRef.current.visibleCount + 1),
      endIndex: Math.min(bars.length - 1, viewportRef.current.endIndex),
    })
    setHover(relative == null ? null : { index: relative, y: point.y })
  }

  const handlePointerUp = () => {
    const drag = dragRef.current
    if (!drag) return
    dragRef.current = null
    if (drag.moved) {
      rememberViewport()
      suppressNextClickRef.current = true
      setHover(null)
      setViewportRevision(value => value + 1)
      return
    }
  }

  const handlePointerCancel = () => {
    rememberViewport()
    dragRef.current = null
    suppressNextClickRef.current = true
    clearPendingClick()
    setHover(null)
    setViewportRevision(value => value + 1)
  }

  const handleCanvasClick = (event: MouseEvent<HTMLCanvasElement>) => {
    if (suppressNextClickRef.current) {
      suppressNextClickRef.current = false
      return
    }
    if (!onBarClick || event.detail === 0) return
    if (event.detail > 1) {
      clearPendingClick()
      return
    }
    const point = pointerPosition(event)
    const index = canvasIndexAtX({
      x: point.x,
      plotLeft: point.plotLeft,
      plotRight: point.plotRight,
      startIndex: Math.max(0, viewportRef.current.endIndex - viewportRef.current.visibleCount + 1),
      endIndex: Math.min(bars.length - 1, viewportRef.current.endIndex),
    })
    if (index == null) return
    if (pendingClickRef.current) {
      const previousTime = pendingClickRef.current.time
      clearPendingClick()
      onBarClick(previousTime)
    }
    const time = bars[index].time
    const pending = { time, timer: 0 }
    pending.timer = window.setTimeout(() => {
      if (pendingClickRef.current !== pending) return
      pendingClickRef.current = null
      onBarClick(time)
    }, SINGLE_CLICK_DELAY_MS)
    pendingClickRef.current = pending
  }

  wheelHandlerRef.current = event => {
    const canvas = canvasRef.current
    if (bars.length === 0 || !canvas) return
    event.preventDefault()
    const point = pointerPosition({ currentTarget: canvas, clientX: event.clientX, clientY: event.clientY })
    const ratio = Math.min(1, Math.max(0, (point.x - point.plotLeft) / Math.max(1, point.plotRight - point.plotLeft)))
    viewportRef.current = zoomCanvasViewport(viewportRef.current,
      event.deltaY < 0 ? 1.2 : 1 / 1.2, bars.length, ratio)
    rememberViewport()
    setHover(null)
    setViewportRevision(value => value + 1)
  }

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const onWheel = (event: globalThis.WheelEvent) => wheelHandlerRef.current(event)
    canvas.addEventListener('wheel', onWheel, { passive: false })
    return () => canvas.removeEventListener('wheel', onWheel)
  }, [])

  const handleDoubleClick = (event: MouseEvent<HTMLCanvasElement>) => {
    clearPendingClick()
    if (bars.length === 0 || !onPriceDoubleClick) return
    const point = pointerPosition(event)
    if (point.y < point.plotTop || point.y > point.priceBottom) return
    const state = viewportRef.current
    const visibleCount = Math.max(1, state.visibleCount)
    const start = Math.max(0, state.endIndex - visibleCount + 1)
    const end = Math.min(bars.length - 1, state.endIndex)
    const extraValues: (number | null | undefined)[] = series
      .filter(item => visibleSeries[item.id])
      .flatMap(item => item.values.slice(start, end + 1))
    for (const { line } of resolveVisiblePriceLines(bars, start, end, priceLines)) extraValues.push(line.value)
    if (typeof linkedPrice === 'number' && Number.isFinite(linkedPrice) && linkedPrice > 0) extraValues.push(linkedPrice)
    const range = resolveCanvasPriceRange(bars, state, extraValues)
    const price = range.high - ((point.y - point.plotTop) / Math.max(1, point.priceBottom - point.plotTop)) * (range.high - range.low)
    const latest = bars[bars.length - 1]
    if (Number.isFinite(price) && price > 0 && latest) onPriceDoubleClick(price, latest.close)
  }

  return <section className="space-y-1.5">
    {showControls && <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px] text-secondary">
      <span className="font-medium text-foreground">{symbol} · {title}</span>
      <label className="flex cursor-pointer items-center gap-1.5">
        <input type="checkbox" checked={showVolume} onChange={event => setShowVolume(event.target.checked)} aria-label="显示成交量" />
        <span>成交量</span>
      </label>
      {series.map(item => <label key={item.id} className="flex cursor-pointer items-center gap-1.5">
        <input type="checkbox" checked={visibleSeries[item.id] ?? item.visibleByDefault ?? true}
          onChange={event => setVisibleSeries(previous => ({ ...previous, [item.id]: event.target.checked }))}
          aria-label={'显示' + item.label} />
        <span style={{ color: item.color }}>{item.label}</span>
      </label>)}
      <button type="button" onClick={fitContent}
        className="ml-auto rounded-btn border border-border px-2.5 py-1 text-secondary hover:text-foreground">
        适配视图
      </button>
    </div>}
    <div className="relative w-full" ref={hostRef}>
      <canvas
        ref={canvasRef}
        className="block w-full touch-none"
        style={{ height: chartHeight, cursor: dragRef.current ? 'grabbing' : 'crosshair' }}
        role="img"
        aria-label={symbol + ' ' + title + ' Canvas K线图，滚轮缩放，拖动平移，十字线查看 OHLCV'}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerCancel}
        onPointerLeave={() => { if (!dragRef.current) setHover(null) }}
        onClick={handleCanvasClick}
        onDoubleClick={handleDoubleClick}
      />
      <div className="pointer-events-none absolute left-2 top-2 max-w-[calc(100%-1rem)] truncate rounded px-1.5 py-1 text-[10px] font-mono"
        style={{ background: theme.infoBarBg, color: theme.textStrong }} aria-live="polite" title={summary}>
        {summary}
      </div>
      {bars.length === 0 && <div role="status" className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-muted">
        暂无有效 K 线数据
      </div>}
    </div>
    {currentBar && <p className="break-words whitespace-normal text-[10px] leading-4 text-secondary" title={summary}>
      {summary}
    </p>}
  </section>
}
