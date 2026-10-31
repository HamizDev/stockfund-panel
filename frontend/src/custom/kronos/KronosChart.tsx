import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { useChartTheme } from '@/lib/theme'
import { useECharts } from '@/pages/backtest/charts/useECharts'
import { buildKronosChartModel, formatKronosAxisTime, formatKronosPrice, formatKronosTime } from './chartModel'
import { KronosMinuteChart } from './KronosMinuteChart'
import type { KronosEvaluationPoint } from './evaluationTypes'
import type { KronosAssetType } from './state'
import type { KronosResult } from './types'

const HISTORY_SERIES_NAME = '真实历史K线'
const FORECAST_OHLC_SERIES_NAME = '预测OHLC'
const FORECAST_CLOSE_SERIES_NAME = '预测收盘'

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, character => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]!)
}

function formatTooltipRows(raw: unknown, result: KronosResult, assetType: KronosAssetType | undefined, forecastStartIndex: number | null): string {
  const rows: any[] = Array.isArray(raw) ? raw : [raw]
  const firstRow = rows[0]
  const axisValue = firstRow?.axisValue
  const time = typeof axisValue === 'string' ? formatKronosTime(axisValue, result.period) : ''
  const prices = rows.flatMap((row: any) => {
    if (row?.seriesName === FORECAST_CLOSE_SERIES_NAME
      && forecastStartIndex !== null
      && typeof row?.dataIndex === 'number'
      && row.dataIndex < forecastStartIndex) {
      return []
    }

    const label = escapeHtml(String(row?.seriesName ?? ''))
    if (row?.seriesType === 'candlestick' && Array.isArray(row.value) && row.value.length >= 4) {
      const [open, close, low, high] = row.value.slice(-4)
      if ([open, close, low, high].every((price: unknown) => typeof price === 'number' && Number.isFinite(price))) {
        return [`${label}: 开 ${formatKronosPrice(open, assetType)} 高 ${formatKronosPrice(high, assetType)} 低 ${formatKronosPrice(low, assetType)} 收 ${formatKronosPrice(close, assetType)}`]
      }
      return []
    }
    const lineValue = Array.isArray(row?.value) ? row.value.at(-1) : row?.value
    if (row?.seriesType === 'line' && typeof lineValue === 'number' && Number.isFinite(lineValue)) {
      return [`${label}: ${formatKronosPrice(lineValue, assetType)}`]
    }
    return []
  })
  return [escapeHtml(time), ...prices].filter(Boolean).join('<br/>')
}

function forecastMarkers(startTime: string, endTime: string) {
  return {
    markArea: {
      silent: true,
      itemStyle: { color: 'rgba(168,85,247,0.09)' },
      label: {
        show: true,
        formatter: '预测区间',
        position: 'insideTop',
        color: '#C084FC',
        fontSize: 9,
      },
      data: [[{ xAxis: startTime }, { xAxis: endTime }]],
    },
    markLine: {
      silent: true,
      symbol: 'none',
      lineStyle: { color: '#A855F7', type: 'dashed', width: 1.5 },
      label: {
        show: true,
        formatter: '预测开始',
        position: 'insideEndTop',
        color: '#D8B4FE',
        fontSize: 9,
        backgroundColor: 'rgba(24,24,27,0.75)',
        padding: [2, 4],
      },
      data: [{ xAxis: startTime }],
    },
  }
}

export function buildKronosChartOption(
  result: KronosResult,
  assetType: KronosAssetType | undefined,
  text: string,
  grid: string,
  border: string,
): EChartsOption {
  const model = buildKronosChartModel(result)
  const hasForecast = model.forecastStartIndex !== null
  const totalCategoryCount = model.categories.length
  const lastCategoryIndex = Math.max(totalCategoryCount - 1, 1)
  const zoomStart = totalCategoryCount > 1 ? (model.defaultStartIndex / lastCategoryIndex) * 100 : 0
  const forecastMarks = hasForecast && model.forecastStartTime && model.forecastEndTime
    ? forecastMarkers(model.forecastStartTime, model.forecastEndTime)
    : undefined

  const series: any[] = [
    {
      name: HISTORY_SERIES_NAME,
      type: 'candlestick',
      data: model.historyOhlc.map(value => value ?? '-'),
      animation: false,
      z: 2,
      itemStyle: {
        color: '#C74040',
        color0: '#2D9B65',
        borderColor: '#C74040',
        borderColor0: '#2D9B65',
      },
    },
  ]

  if (hasForecast && forecastMarks && model.forecastMode === 'ohlc') {
    series.push({
      name: FORECAST_OHLC_SERIES_NAME,
      type: 'candlestick',
      data: model.forecastOhlc.map(value => value ?? '-'),
      animation: false,
      z: 3,
      itemStyle: {
        color: 'rgba(199,64,64,0.42)',
        color0: 'rgba(45,155,101,0.42)',
        borderColor: '#C74040',
        borderColor0: '#2D9B65',
        borderColorDoji: '#A855F7',
        borderType: 'dashed',
        borderWidth: 1.2,
      },
      ...forecastMarks,
    })
  } else if (hasForecast && forecastMarks && model.forecastMode === 'close_only') {
    series.push({
      name: FORECAST_CLOSE_SERIES_NAME,
      type: 'line',
      data: model.forecastClose,
      showSymbol: true,
      symbol: 'circle',
      symbolSize: 4,
      connectNulls: false,
      lineStyle: { width: 2, color: '#A855F7', type: 'dashed' },
      itemStyle: { color: '#A855F7' },
      ...forecastMarks,
    })
  }

  const legendData = [HISTORY_SERIES_NAME]
  if (hasForecast) {
    legendData.push(model.forecastMode === 'ohlc' ? FORECAST_OHLC_SERIES_NAME : FORECAST_CLOSE_SERIES_NAME)
  }

  return {
    animation: false,
    color: ['#C74040', '#2D9B65', '#A855F7'],
    grid: { left: 56, right: 8, top: 34, bottom: 48 },
    legend: {
      top: 0,
      textStyle: { color: text, fontSize: 9 },
      data: legendData,
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(24,24,27,0.95)',
      borderColor: 'rgba(255,255,255,0.1)',
      textStyle: { color: '#E4E4E7', fontSize: 11 },
      formatter: (raw: unknown) => formatTooltipRows(raw, result, assetType, model.forecastStartIndex),
    },
    xAxis: {
      type: 'category',
      data: model.categories,
      boundaryGap: true,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: border } },
      axisLabel: {
        color: text,
        fontSize: 9,
        hideOverlap: true,
        formatter: (value: string) => formatKronosAxisTime(value, result.period),
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: {
        color: text,
        fontSize: 9,
        formatter: (value: number) => formatKronosPrice(value, assetType),
      },
      splitLine: { lineStyle: { color: grid } },
    },
    dataZoom: [
      { type: 'inside', start: zoomStart, end: 100, filterMode: 'filter' },
      { type: 'slider', start: zoomStart, end: 100, height: 16, bottom: 4, borderColor: border, fillerColor: 'rgba(168,85,247,0.14)', textStyle: { color: text, fontSize: 8 }, filterMode: 'filter' },
    ],
    series,
  }
}

function KronosDailyChart({ result, assetType }: { result: KronosResult; assetType?: KronosAssetType }) {
  const theme = useChartTheme()
  const option = useMemo(
    () => buildKronosChartOption(result, assetType, theme.text, theme.grid, theme.border),
    [result, assetType, theme.text, theme.grid, theme.border],
  )
  const chartRef = useECharts(option)
  return <div ref={chartRef} className="h-[300px] w-full min-w-0 max-w-full sm:h-[360px]" aria-label="Kronos 历史与预测价格图" />
}

export function KronosChart({ result, assetType, actualPoints }: {
  result: KronosResult
  assetType?: KronosAssetType
  actualPoints?: KronosEvaluationPoint[]
}) {
  return result.period === '1m'
    ? <KronosMinuteChart result={result} assetType={assetType} actualPoints={actualPoints} />
    : <KronosDailyChart result={result} assetType={assetType} />
}
