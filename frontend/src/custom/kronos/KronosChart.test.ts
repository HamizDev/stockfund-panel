import { describe, expect, it } from 'vitest'
import type { EChartsOption } from 'echarts'
import { buildKronosChartOption } from './KronosChart'
import { buildKronosMinuteChartOption } from './KronosMinuteChart'
import { parseKronosJob, type KronosResult } from './types'

const historyDaily = [{ time: '2026-10-30', open: 100, high: 103, low: 99, close: 102 }]
const partialWarning = (count: number) =>
  `模型预测有${count}根OHLC关系无效；日K对应位置留空，不修正或补造开高低价，收盘价仅保留供误差核对。`

function resultFor(period: 'daily' | '1m', forecastMode: string, forecast: unknown[], warnings: string[] = []): KronosResult {
  const horizon = period === 'daily' ? 5 : 1
  const request = {
    symbol: '000001.SZ',
    name: '测试标的',
    model: 'base',
    period,
    lookback: 60,
    horizon,
    samples: 1,
  }
  const result = {
    symbol: request.symbol,
    name: request.name,
    model: request.model,
    period,
    forecast_mode: forecastMode,
    history: period === 'daily'
      ? historyDaily
      : [{ time: '2026-10-30T09:30:00', open: 100, high: 103, low: 99, close: 102 }],
    forecast,
    input_as_of: period === 'daily' ? '2026-10-30' : '2026-10-30T09:30:00',
    requested_at: '2026-11-01T10:00:00+08:00',
    price_basis: '测试数据',
    warnings,
    metrics: { elapsed_seconds: 1, cuda_peak_allocated_mb: 1, cuda_peak_reserved_mb: 1 },
  }
  return parseKronosJob({
    id: 'job-1',
    state: 'complete',
    request,
    requested_at: '2026-11-01T10:00:00+08:00',
    message: '预测完成',
    error: null,
    result,
  }).result!
}

function seriesOf(option: EChartsOption): Array<{
  name?: string
  type?: string
  data?: unknown[]
  itemStyle?: { color?: string; borderColor?: string }
}> {
  const series = option.series
  return (Array.isArray(series) ? series : series ? [series] : []) as unknown as Array<{ name?: string; type?: string; data?: unknown[] }>
}

function tooltipFormatter(option: EChartsOption): (raw: unknown) => string {
  return (option.tooltip as unknown as { formatter: (raw: unknown) => string }).formatter
}

describe('Kronos chart forecast modes', () => {
  it('renders valid daily OHLC forecasts as candles', () => {
    const result = resultFor('daily', 'ohlc', [
      { time: '2026-11-02', open: 102, high: 105, low: 101, close: 104 },
    ])
    const series = seriesOf(buildKronosChartOption(result, 'stock', '#fff', '#333', '#555'))

    expect(series.map(item => item.type)).toEqual(['candlestick', 'candlestick'])
    expect(series[1].data).toEqual(['-', [102, 104, 101, 105]])
  })

  it('leaves only invalid daily forecast bars blank in partial OHLC mode', () => {
    const result = resultFor('daily', 'partial_ohlc', [
      { time: '2026-11-02', open: 102, high: 105, low: 101, close: 104 },
      { time: '2026-11-03', close: 105 },
    ], [partialWarning(1)])
    const option = buildKronosChartOption(result, 'stock', '#fff', '#333', '#555')
    const series = seriesOf(option)
    const closeOnly = series.find(item => item.name === '仅预测收盘（无OHLC）')

    expect(result.warnings[0]).toContain('1根')
    expect(series.map(item => item.type)).toEqual(['candlestick', 'candlestick', 'scatter'])
    expect(series[1].data).toEqual(['-', [102, 104, 101, 105], '-'])
    expect(closeOnly?.itemStyle).toMatchObject({ color: 'rgba(168,85,247,0)', borderColor: '#A855F7' })
    expect((option.xAxis as unknown as { data?: string[] }).data).toEqual([
      '2026-10-30', '2026-11-02', '2026-11-03',
    ])
    expect(closeOnly?.data).toEqual(['-', '-', ['2026-11-03', 105]])
    expect(tooltipFormatter(option)([{
      axisValue: '2026-11-03',
      seriesName: '仅预测收盘（无OHLC）',
      seriesType: 'scatter',
      value: ['2026-11-03', 105],
    }])).toContain('仅预测收盘（无OHLC）: 收 105.00')
  })

  it('keeps all-invalid daily candles blank and shows only hollow close points', () => {
    const result = resultFor('daily', 'partial_ohlc', [
      { time: '2026-11-02', close: 104 },
      { time: '2026-11-03', close: 105 },
    ], [partialWarning(2)])
    const option = buildKronosChartOption(result, 'stock', '#fff', '#333', '#555')
    const series = seriesOf(option)
    const closeOnly = series.find(item => item.name === '仅预测收盘（无OHLC）')

    expect(series.map(item => item.type)).toEqual(['candlestick', 'scatter'])
    expect(closeOnly?.data).toEqual([
      '-', ['2026-11-02', 104], ['2026-11-03', 105],
    ])
    expect((closeOnly as unknown as { markArea?: object })?.markArea).toBeDefined()
  })

  it('shows old daily close-only results as hollow points without a line', () => {
    const result = resultFor('daily', 'close_only', [
      { time: '2026-11-02', close: 104 },
    ], ['仅展示收盘价预测路径'])
    const option = buildKronosChartOption(result, 'stock', '#fff', '#333', '#555')
    const series = seriesOf(option)

    expect(series.map(item => item.type)).toEqual(['candlestick', 'scatter'])
    expect(series[1].name).toBe('仅预测收盘（无OHLC）')
    expect(series[1].data).toEqual(['-', ['2026-11-02', 104]])
    expect((option.xAxis as unknown as { data?: string[] }).data).toEqual(['2026-10-30', '2026-11-02'])
    expect((series[1] as unknown as { markArea?: object }).markArea).toBeDefined()
  })

  it('shows all 20 daily close values as hollow points without manufacturing candles', () => {
    const forecast = Array.from({ length: 20 }, (_, index) => ({
      time: '2026-11-' + String(index + 2).padStart(2, '0'),
      close: 105 + index * 0.1,
    }))
    const result = resultFor('daily', 'partial_ohlc', forecast, [partialWarning(20)])
    const option = buildKronosChartOption(result, 'stock', '#fff', '#333', '#555')
    const series = seriesOf(option)

    expect(series.map(item => item.type)).toEqual(['candlestick', 'scatter'])
    expect(series[1].data?.filter(value => value !== '-')).toHaveLength(20)
  })

  it('keeps the one-minute forecast on its close-price line, including partial OHLC rows', () => {
    const result = resultFor('1m', 'partial_ohlc', [
      { time: '2026-10-30T09:31:00', open: 102, high: 104, low: 101, close: 103 },
      { time: '2026-10-30T09:32:00', close: 104 },
    ], [partialWarning(1)])
    const forecast = seriesOf(buildKronosMinuteChartOption(result, 'stock', '#fff', '#333', '#555'))
      .find(item => item.name === '预测收盘')

    expect(forecast?.type).toBe('line')
    expect(forecast?.data).toContain(103)
    expect(forecast?.data).toContain(104)
  })

  it('rejects residual OHLC fields on a partial-mode gap', () => {
    expect(() => resultFor('daily', 'partial_ohlc', [
      { time: '2026-11-02', open: 102, close: 104 },
    ], [partialWarning(1)])).toThrow()
  })

  it('requires an explanatory warning for partial OHLC results', () => {
    expect(() => resultFor('daily', 'partial_ohlc', [
      { time: '2026-11-02', close: 104 },
    ])).toThrow()
  })

  it('keeps strict OHLC validation for full OHLC mode', () => {
    expect(() => resultFor('daily', 'ohlc', [
      { time: '2026-11-02', open: 105, high: 104, low: 101, close: 103 },
    ])).toThrow()
  })
})
