import { useMemo, useState } from 'react'
import type { EChartsOption } from 'echarts'
import { useChartTheme } from '@/lib/theme'
import { useECharts } from '@/pages/backtest/charts/useECharts'
import { formatKronosPrice, formatKronosTime } from './chartModel'
import {
  buildKronosMinuteChartModel,
  KRONOS_SESSION_BREAK_PREFIX,
  type KronosMinuteChartModel,
} from './minuteChartModel'
import type { KronosAssetType } from './state'
import type { KronosResult } from './types'
import type { KronosEvaluationPoint } from './evaluationTypes'

const HISTORY_SERIES_NAME = '真实收盘'
const FORECAST_SERIES_NAME = '预测收盘'
const VOLUME_SERIES_NAME = '真实成交量'
const VWAP_SERIES_NAME = '真实VWAP'
const UP_COLOR = '#C74040'
const DOWN_COLOR = '#2D9B65'
const NEUTRAL_COLOR = '#A1A1AA'
const FORECAST_COLOR = '#A855F7'
const VWAP_COLOR = '#F59E0B'
const REALIZED_COLOR = '#38BDF8'
const REALIZED_SERIES_NAME = '事后真实收盘'

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, character => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]!)
}

function formatMetric(value: number): string {
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

export function formatKronosMinuteTooltip(
  raw: unknown,
  model: KronosMinuteChartModel,
  assetType?: KronosAssetType,
): string {
  const rows = Array.isArray(raw) ? raw.filter(isRecord) : isRecord(raw) ? [raw] : []
  const firstRow = rows[0]
  const axisValue = firstRow?.axisValue ?? firstRow?.axisValueLabel ?? firstRow?.name
  const category = typeof axisValue === 'number'
    ? model.categories[axisValue]
    : typeof axisValue === 'string' ? axisValue : ''
  if (!category || category.startsWith(KRONOS_SESSION_BREAK_PREFIX)) return ''

  const formattedTime = escapeHtml(formatKronosTime(category, '1m'))
  const actual = model.historyByTime.get(category)
  const forecast = model.forecastByTime.get(category)
  const realized = model.realizedByTime.get(category)
  const details: string[] = []

  if (actual) {
    details.push(`${HISTORY_SERIES_NAME}: 开 ${formatKronosPrice(actual.open, assetType)} 高 ${formatKronosPrice(actual.high, assetType)} 低 ${formatKronosPrice(actual.low, assetType)} 收 ${formatKronosPrice(actual.close, assetType)}`)
    if (typeof actual.volume === 'number' && Number.isFinite(actual.volume) && actual.volume >= 0) {
      details.push(`${VOLUME_SERIES_NAME}: ${formatMetric(actual.volume)}`)
    }
    if (typeof actual.amount === 'number' && Number.isFinite(actual.amount) && actual.amount >= 0) {
      details.push(`成交额: ${formatMetric(actual.amount)}`)
    }
  }

  if (forecast) details.push(`${FORECAST_SERIES_NAME}: ${formatKronosPrice(forecast.close, assetType)}`)
  if (realized !== undefined) {
    details.push(`${REALIZED_SERIES_NAME}: ${formatKronosPrice(realized, assetType)}`)
    if (forecast) details.push(`实际 − 预测: ${(realized - forecast.close).toFixed(6)}`)
  }
  return [formattedTime, ...details].join('<br/>')
}

function axisTimeLabel(value: string, model: KronosMinuteChartModel): string {
  if (value.startsWith(KRONOS_SESSION_BREAK_PREFIX)) return ''
  const date = value.slice(0, 10)
  const minute = value.slice(11, 16)
  if (model.dates.length > 1 && (value === model.historyByTime.keys().next().value
    || value === model.forecastStartTime || value === model.forecastEndTime)) return `${date.slice(5)}\n${minute}`
  if (minute === '09:30') return model.dates.length > 1 ? `${date} 9:30` : '9:30'
  if (minute === '10:30') return '10:30'
  if (minute === '11:30') return '11:30/13:00'
  if (minute === '13:00') return ''
  if (minute === '14:00') return '14:00'
  if (minute === '15:00') return '15:00'
  return ''
}

function formatPercent(value: number): string {
  if (Math.abs(value) < 0.005) return '0.00%'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

export function buildKronosMinuteChartOption(
  result: KronosResult,
  assetType: KronosAssetType | undefined,
  text: string,
  gridColor: string,
  border: string,
  fullRange = false,
  actualPoints: KronosEvaluationPoint[] = [],
  model: KronosMinuteChartModel = buildKronosMinuteChartModel(result, actualPoints),
): EChartsOption {
  const baseline = typeof result.prev_close === 'number' && Number.isFinite(result.prev_close) && result.prev_close > 0
    ? result.prev_close
    : null
  const priceValues = [
    ...model.historyClose,
    ...model.forecastClose,
    ...model.realizedClose,
    ...(model.hasVwap ? model.vwap : []),
  ].filter((value): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0)
  const maximumDeviation = baseline === null || priceValues.length === 0
    ? 0
    : Math.max(...priceValues.map(value => Math.abs(value - baseline)))
  const maxDiff = baseline === null ? null : Math.max(maximumDeviation * 1.1, baseline * 0.001)
  const latestHistoricalClose = model.historyByTime.get(model.latestHistoryTime ?? '')?.close
  const direction = baseline === null
    ? 0
    : (latestHistoricalClose ?? baseline) - baseline
  const historyColor = direction > 0 ? UP_COLOR : direction < 0 ? DOWN_COLOR : NEUTRAL_COLOR
  const areaColor = direction > 0
    ? 'rgba(199,64,64,0.32)'
    : direction < 0 ? 'rgba(45,155,101,0.32)' : 'rgba(161,161,170,0.24)'
  const axisLeft = 50
  const axisRight = baseline === null ? 12 : 52
  const validForecastRange = model.forecastStartTime !== null && model.forecastEndTime !== null

  const historyMarkLines: any[] = baseline === null ? [] : [{
    yAxis: baseline,
    lineStyle: { color: border, type: 'dashed', width: 1 },
    label: { show: false },
    symbol: 'none',
  }]
  const forecastMarks = validForecastRange ? {
    markArea: {
      silent: true,
      itemStyle: { color: 'rgba(168,85,247,0.09)' },
      label: { show: true, formatter: '预测区间', position: 'insideTop', color: '#C084FC', fontSize: 9 },
      data: [[{ xAxis: model.forecastStartTime! }, { xAxis: model.forecastEndTime! }]],
    },
    markLine: {
      silent: true,
      symbol: 'none',
      lineStyle: { color: FORECAST_COLOR, type: 'dashed', width: 1.5 },
      label: {
        show: true,
        formatter: '预测开始',
        position: 'insideEndTop',
        color: '#D8B4FE',
        fontSize: 9,
        backgroundColor: 'rgba(24,24,27,0.75)',
        padding: [2, 4],
      },
      data: [{ xAxis: model.forecastStartTime }],
    },
  } : undefined

  const series: any[] = [
    {
      name: HISTORY_SERIES_NAME,
      type: 'line',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: model.historyClose,
      smooth: false,
      symbol: 'none',
      connectNulls: false,
      lineStyle: { width: 1.4, color: historyColor },
      itemStyle: { color: historyColor },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: areaColor },
            { offset: 1, color: 'rgba(0,0,0,0)' },
          ],
        },
      },
      markLine: historyMarkLines.length > 0 ? { silent: true, symbol: 'none', data: historyMarkLines } : undefined,
    },
  ]

  if (model.forecastStartTime) {
    series.push({
      name: FORECAST_SERIES_NAME,
      type: 'line',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: model.forecastClose,
      smooth: false,
      showSymbol: true,
      symbol: 'circle',
      symbolSize: 3,
      connectNulls: false,
      lineStyle: { width: 2, color: FORECAST_COLOR, type: 'dashed' },
      itemStyle: { color: FORECAST_COLOR },
      ...(forecastMarks ?? {}),
    })
  }

  if (model.realizedByTime.size > 0) {
    series.push({
      name: REALIZED_SERIES_NAME,
      type: 'line',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: model.realizedClose,
      smooth: false,
      symbol: 'circle',
      symbolSize: 3,
      connectNulls: false,
      lineStyle: { width: 2, color: REALIZED_COLOR },
      itemStyle: { color: REALIZED_COLOR },
    })
  }

  series.push({
    name: VOLUME_SERIES_NAME,
    type: 'bar',
    xAxisIndex: 1,
    yAxisIndex: 1,
    data: model.volumes,
    barMaxWidth: 5,
    animation: false,
  })

  if (model.hasVwap) {
    series.push({
      name: VWAP_SERIES_NAME,
      type: 'line',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: model.vwap,
      smooth: false,
      symbol: 'none',
      connectNulls: false,
      lineStyle: { width: 1, color: VWAP_COLOR },
      itemStyle: { color: VWAP_COLOR },
    })
  }

  const yAxis: any[] = [
    {
      type: 'value',
      gridIndex: 0,
      scale: true,
      min: baseline === null ? undefined : baseline - maxDiff!,
      max: baseline === null ? undefined : baseline + maxDiff!,
      interval: baseline === null ? undefined : maxDiff! / 2,
      splitNumber: 4,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: text,
        fontSize: 9,
        fontFamily: 'JetBrains Mono, monospace',
        formatter: (value: number) => formatKronosPrice(value, assetType),
      },
      axisPointer: {
        label: {
          formatter: (params: { value?: unknown }) => typeof params.value === 'number'
            ? formatKronosPrice(params.value, assetType)
            : '',
        },
      },
      splitLine: { lineStyle: { color: gridColor } },
    },
    {
      type: 'value',
      gridIndex: 1,
      scale: true,
      splitNumber: 2,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { show: false },
      splitLine: { show: false },
    },
  ]
  if (baseline !== null && maxDiff !== null) {
    const percentDiff = maxDiff / baseline * 100
    yAxis.push({
      type: 'value',
      gridIndex: 0,
      position: 'right',
      min: -percentDiff,
      max: percentDiff,
      interval: percentDiff / 2,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: text, fontSize: 9, fontFamily: 'JetBrains Mono, monospace', formatter: formatPercent },
      axisPointer: { label: { formatter: (params: { value?: unknown }) => typeof params.value === 'number' ? formatPercent(params.value) : '' } },
      splitLine: { show: false },
    })
  }

  const xAxis: any[] = [
    {
      type: 'category',
      gridIndex: 0,
      data: model.categories,
      boundaryGap: false,
      axisLine: { lineStyle: { color: border } },
      axisTick: { show: false },
      axisLabel: {
        color: text,
        fontSize: 9,
        fontFamily: 'JetBrains Mono, monospace',
        interval: 0,
        hideOverlap: true,
        formatter: (value: string) => axisTimeLabel(value, model),
      },
      axisPointer: {
        show: true,
        lineStyle: { color: border, type: 'dashed', width: 1 },
        label: { show: true, color: text, backgroundColor: 'rgba(24,24,27,0.9)', formatter: (params: { value?: unknown }) => typeof params.value === 'string' ? params.value : '' },
      },
      splitLine: { show: false },
    },
    {
      type: 'category',
      gridIndex: 1,
      data: model.categories,
      boundaryGap: false,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { show: false },
      axisPointer: {
        show: true,
        lineStyle: { color: border, type: 'dashed', width: 1 },
        label: { show: false },
      },
      splitLine: { show: false },
    },
  ]

  return {
    animation: false,
    backgroundColor: 'transparent',
    color: [historyColor, FORECAST_COLOR, UP_COLOR, VWAP_COLOR],
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: axisLeft, right: axisRight, top: 24, height: '58%', containLabel: false },
      { left: axisLeft, right: axisRight, top: '73%', height: '21%', containLabel: false },
    ],
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: 'rgba(24,24,27,0.95)',
      borderColor: 'rgba(255,255,255,0.1)',
      textStyle: { color: '#E4E4E7', fontSize: 11 },
      axisPointer: { type: 'cross', animation: false, lineStyle: { color: border, type: 'dashed', width: 1 } },
      formatter: (raw: unknown) => formatKronosMinuteTooltip(raw, model, assetType),
    },
    dataZoom: [{
      type: 'inside',
      xAxisIndex: [0, 1],
      ...(model.dates.length > 1 && !fullRange && model.latestHistoryTime && model.forecastEndTime
        ? { startValue: Math.max(0, model.categoryIndexByTime.get(model.latestHistoryTime)! - 119),
            endValue: model.categoryIndexByTime.get(model.forecastEndTime) }
        : { start: 0, end: 100 }),
      filterMode: 'filter',
      zoomOnMouseWheel: true,
      moveOnMouseMove: true,
    }],
    xAxis,
    yAxis,
    series,
  }
}

function LegendItem({ color, label, dashed = false, bar = false }: { color: string; label: string; dashed?: boolean; bar?: boolean }) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1 whitespace-nowrap">
      <span
        aria-hidden="true"
        className={bar ? 'inline-block h-1.5 w-2.5 rounded-sm' : 'inline-block w-3 border-t-2'}
        style={bar ? { backgroundColor: color } : { borderColor: color, borderStyle: dashed ? 'dashed' : 'solid' }}
      />
      <span>{label}</span>
    </span>
  )
}

export function KronosMinuteChart({ result, assetType, actualPoints }: {
  result: KronosResult
  assetType?: KronosAssetType
  actualPoints?: KronosEvaluationPoint[]
}) {
  const theme = useChartTheme()
  const [fullRange, setFullRange] = useState(false)
  const model = useMemo(() => buildKronosMinuteChartModel(result, actualPoints), [result, actualPoints])
  const option = useMemo(
    () => buildKronosMinuteChartOption(result, assetType, theme.text, theme.grid, theme.border, fullRange, actualPoints, model),
    [result, assetType, theme.text, theme.grid, theme.border, fullRange, actualPoints, model],
  )
  const chartRef = useECharts(option)
  const hasBaseline = typeof result.prev_close === 'number' && Number.isFinite(result.prev_close) && result.prev_close > 0
  const latestClose = model.historyByTime.get(model.latestHistoryTime ?? '')?.close
  const historyLegendColor = !hasBaseline || latestClose === undefined
    ? NEUTRAL_COLOR
    : latestClose > result.prev_close! ? UP_COLOR : latestClose < result.prev_close! ? DOWN_COLOR : NEUTRAL_COLOR
  const baselineCaption = hasBaseline
    ? `相对${result.prev_close_date ? `${result.prev_close_date} 收盘` : '图示昨收'}基准；跨日预测沿用`
    : '缺少昨收基准，仅显示价格'

  return (
    <div className="w-full min-w-0 max-w-full overflow-hidden">
      <div className="mb-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-1 font-mono text-[10px] text-muted">
        <span>{baselineCaption}</span>
        <LegendItem color={historyLegendColor} label={HISTORY_SERIES_NAME} />
        {model.forecastStartTime && <LegendItem color={FORECAST_COLOR} label={FORECAST_SERIES_NAME} dashed />}
        {model.realizedByTime.size > 0 && <LegendItem color={REALIZED_COLOR} label={REALIZED_SERIES_NAME} />}
        <LegendItem color={UP_COLOR} label={VOLUME_SERIES_NAME} bar />
        {model.hasVwap && <LegendItem color={VWAP_COLOR} label={VWAP_SERIES_NAME} />}
        {model.dates.length > 1 && <>
          <span>{model.dates[0]} → {model.dates.at(-1)}</span>
          <button type="button" className="ml-auto rounded border border-border px-1.5 py-0.5 text-accent hover:bg-elevated"
            aria-pressed={fullRange} onClick={() => setFullRange(value => !value)}
            title="跨日时默认聚焦最近120根已观测分钟及预测，避免未形成时段挤压曲线">
            {fullRange ? '聚焦预测' : '全区间'}
          </button>
        </>}
      </div>
      <div
        ref={chartRef}
        className="h-[300px] w-full min-w-0 max-w-full sm:h-[360px]"
        aria-label="Kronos 分钟历史与预测价格图"
      />
    </div>
  )
}
