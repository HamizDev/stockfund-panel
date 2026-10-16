import { useMemo } from 'react'
import { CanvasMarketChart, type CanvasChartMarker, type CanvasChartPriceLine, type CanvasChartSeries } from '@/components/CanvasMarketChart'
import { chartPrice } from '@/lib/chartPrice'
import { calculateDonchianChannels } from '@/lib/canvas-chart/donchian'
import type { ChartMarker, ChartPriceLine } from '@/components/EChartsCandlestick'
import {
  MOVING_AVERAGE_KEYS,
  normalizeTradingViewDailyRows,
  type DailyKlineInput,
  type MovingAverageKey,
} from './tradingview-daily'

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
  anchoredVwap?: Array<{ date: string; value: number | null }>
}

/** Historical export name is kept for source compatibility; rendering is owned by CanvasMarketChart. */
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
  anchoredVwap,
}: TradingViewDailyChartProps) {
  const validated = useMemo(() => normalizeTradingViewDailyRows(data), [data])
  const availableDates = useMemo(() => new Set(validated.bars.map(bar => bar.date)), [validated.bars])
  const anchoredByDate = useMemo(() => new Map((anchoredVwap ?? []).map(point => [point.date,
    point.value != null && Number.isFinite(point.value) && point.value > 0 ? point.value : null])), [anchoredVwap])
  const bars = useMemo(() => validated.bars.map(bar => ({
    time: bar.date,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume,
  })), [validated.bars])
  const donchian20 = useMemo(() => calculateDonchianChannels(validated.bars), [validated.bars])
  const series = useMemo<CanvasChartSeries[]>(() => {
    const movingAverages: CanvasChartSeries[] = MOVING_AVERAGE_KEYS.map(key => ({
      id: key,
      label: MOVING_AVERAGE_LABELS[key],
      color: MOVING_AVERAGE_COLORS[key],
      values: validated.bars.map(bar => bar[key]),
      visibleByDefault: showMA,
    }))
    return [
      ...movingAverages,
      { id: 'dc20-upper', label: 'DC20上', color: '#A78BFA', values: donchian20.upper, visibleByDefault: false },
      { id: 'dc20-lower', label: 'DC20下', color: '#C4B5FD', values: donchian20.lower, visibleByDefault: false },
      { id: 'boll-upper', label: 'BOLL上轨', color: '#38BDF8', values: validated.bars.map(bar => bar.bollUpper), visibleByDefault: false },
      { id: 'boll-lower', label: 'BOLL下轨', color: '#38BDF8', values: validated.bars.map(bar => bar.bollLower), visibleByDefault: false },
      { id: 'anchored-vwap', label: '锚定VWAP', color: '#F59E0B',
        values: validated.bars.map(bar => anchoredByDate.get(bar.date) ?? null),
        visibleByDefault: true },
    ]
  }, [anchoredByDate, donchian20, showMA, validated.bars])
  const canvasMarkers = useMemo<CanvasChartMarker[]>(() => (markers ?? [])
    .filter(marker => availableDates.has(marker.date))
    .map(marker => ({
      time: marker.date,
      position: marker.above || marker.kind === 'sell' ? 'above' : 'below',
      shape: marker.kind === 'neutral' ? 'circle' : 'arrow',
      color: marker.color ?? (marker.kind === 'buy' ? '#C74040' : marker.kind === 'sell' ? '#2D9B65' : '#A1A1AA'),
      label: marker.label,
    })), [markers, availableDates])
  const canvasPriceLines = useMemo<CanvasChartPriceLine[]>(() => (priceLines ?? []).map(line => ({
    value: line.value,
    label: line.label,
    color: line.color,
    start: line.start,
    end: line.end,
    dash: 'dashed',
  })), [priceLines])

  return <section className="space-y-1.5">
    <CanvasMarketChart
      bars={bars}
      symbol={symbol}
      title="日K"
      contextKey={contextKey}
      height={height}
      visibleBars={visibleBars}
      showControls={showControls}
      series={series}
      markers={canvasMarkers}
      priceLines={canvasPriceLines}
      linkedPrice={linkedPrice}
      upColor="#C74040"
      downColor="#2D9B65"
      formatPrice={value => chartPrice(value, symbol)}
      formatTime={time => String(time)}
      extraSummary={index => {
        const atr = validated.bars[index]?.atr14
        return atr == null ? '' : 'ATR14 ' + chartPrice(atr, symbol)
      }}
      onBarClick={time => { if (typeof time === 'string') onDateClick?.(time) }}
      onPriceDoubleClick={onPriceDoubleClick}
    />
    <p className="sr-only">
      蜡烛颜色使用A股习惯，红色表示收盘价不低于开盘价，绿色表示收盘价低于开盘价。无效OHLC不绘制，缺失或无效成交量不绘制对应成交量柱。可滚轮缩放、拖动平移并用十字线查看日期和OHLCV。
    </p>
    {validated.invalidRows > 0 && <p role="status" className="text-[10px] leading-4 text-muted">
      已忽略 {validated.invalidRows} 条日期或OHLC无效的K线。
    </p>}
    {validated.conflictingDates > 0 && <p role="status" className="text-[10px] leading-4 text-muted">
      {validated.conflictingDates} 个日期存在互相冲突的重复K线，已隐藏这些日期。
    </p>}
    {validated.missingVolumeBars > 0 && <p role="status" className="text-[10px] leading-4 text-muted">
      {validated.missingVolumeBars} 根有效价格K线缺少有效成交量；保留蜡烛，仅省略对应成交量柱。
    </p>}
  </section>
}
