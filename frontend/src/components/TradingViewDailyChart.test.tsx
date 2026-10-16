import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { ComponentProps } from 'react'
import type { CanvasMarketChartProps } from '@/components/CanvasMarketChart'
import type { ChartMarker, ChartPriceLine } from '@/components/EChartsCandlestick'
import { TradingViewDailyChart } from './TradingViewDailyChart'
import type { DailyKlineInput } from './tradingview-daily'

const mocked = vi.hoisted(() => ({ props: null as CanvasMarketChartProps | null }))

vi.mock('@/components/CanvasMarketChart', async () => {
  const React = await import('react')
  return {
    CanvasMarketChart: (props: CanvasMarketChartProps) => {
      mocked.props = props
      return React.createElement('div', { 'data-testid': 'canvas-market-chart' })
    },
  }
})

function getRenderedCanvasProps(): CanvasMarketChartProps {
  const props = mocked.props
  if (props === null) throw new Error('CanvasMarketChart was not rendered')
  return props
}

const testRows: DailyKlineInput[] = [
  { date: '2026-09-28', open: 10, high: 12, low: 9, close: 11, volume: null, ma5: 10.5 },
  { date: '2026-09-30', open: 11, high: 13, low: 10, close: 12, volume: 200, ma5: 11 },
]

function renderChart(props: Partial<ComponentProps<typeof TradingViewDailyChart>> = {}) {
  mocked.props = null
  const html = renderToStaticMarkup(
    <TradingViewDailyChart
      data={testRows}
      symbol="510300.SH"
      contextKey="510300.SH|2026-09-01|2026-09-30"
      {...props}
    />,
  )
  return { html, canvas: getRenderedCanvasProps() }
}

describe('TradingViewDailyChart Canvas adapter', () => {
  it('validates OHLC, preserves valid candles, and forwards missing volume as null', () => {
    const { html, canvas } = renderChart({
      data: [
        ...testRows,
        { ...testRows[1], date: '2026-10-01', volume: -1 },
        { date: '2026-10-02', open: 10, high: 9, low: 11, close: 10 },
      ],
    })

    expect(canvas.title).toBe('日K')
    expect(canvas.contextKey).toBe('510300.SH|2026-09-01|2026-09-30')
    expect(canvas.bars).toEqual([
      { time: '2026-09-28', open: 10, high: 12, low: 9, close: 11, volume: null },
      { time: '2026-09-30', open: 11, high: 13, low: 10, close: 12, volume: 200 },
      { time: '2026-10-01', open: 11, high: 13, low: 10, close: 12, volume: null },
    ])
    expect(canvas.series?.find(series => series.id === 'ma5')?.values).toEqual([10.5, 11, 11])
    expect(html).toContain('已忽略 1 条日期或OHLC无效的K线')
    expect(html).toContain('2 根有效价格K线缺少有效成交量')
    expect(html).not.toContain('Copyright')
    expect(html).not.toContain('href="https://www.tradingview.com/"')
  })

  it('keeps markers only on exact available dates', () => {
    const markers: ChartMarker[] = [
      { date: '2026-09-29', kind: 'buy', label: '相邻日' },
      { date: '2026-09-30', kind: 'buy', label: '买入' },
    ]
    const { canvas } = renderChart({ markers })

    expect(canvas.markers).toEqual([{
      time: '2026-09-30', position: 'below', shape: 'arrow', color: '#C74040', label: '买入',
    }])
  })

  it('forwards exact-date clicks, double-click prices, levels, and anchored VWAP to Canvas', () => {
    const onDateClick = vi.fn()
    const onPriceDoubleClick = vi.fn()
    const priceLines: ChartPriceLine[] = [{ value: 10.5, label: '支撑', color: '#38bdf8' }]
    const { canvas } = renderChart({
      onDateClick,
      onPriceDoubleClick,
      priceLines,
      anchoredVwap: [
        { date: '2026-09-28', value: 10.5 },
        { date: '2026-09-30', value: null },
      ],
    })

    canvas.onBarClick?.('2026-09-30')
    canvas.onPriceDoubleClick?.(10.5, 12)
    expect(onDateClick).toHaveBeenCalledWith('2026-09-30')
    expect(onPriceDoubleClick).toHaveBeenCalledWith(10.5, 12)
    expect(canvas.priceLines).toEqual([{
      value: 10.5, label: '支撑', color: '#38bdf8', start: undefined, end: undefined, dash: 'dashed',
    }])
    expect(canvas.series?.find(series => series.id === 'anchored-vwap')?.values).toEqual([10.5, null])
  })
})
