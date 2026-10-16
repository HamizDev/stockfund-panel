import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { ComponentProps } from 'react'
import type { CanvasMarketChartProps } from '@/components/CanvasMarketChart'
import { CryptoMarketChart } from './CryptoMarketChart'
import type { CryptoMarketBar, CryptoMarketChartLevel } from './CryptoMarketChart'

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

const testBars = (endTime = 60_000): CryptoMarketBar[] => [
  { open_time_ms: 0, close_time_ms: 59_999, open: '100', high: '110', low: '90', close: '105', volume: '2' },
  { open_time_ms: endTime, close_time_ms: endTime + 59_999, open: '105', high: '112', low: '101', close: '108', volume: '3' },
]

function renderChart(props: Partial<ComponentProps<typeof CryptoMarketChart>> = {}) {
  mocked.props = null
  const html = renderToStaticMarkup(
    <CryptoMarketChart bars={testBars()} interval="1m" symbol="BTCUSDT" {...props} />,
  )
  if (!mocked.props) throw new Error('CanvasMarketChart was not rendered')
  return { html, canvas: mocked.props }
}

describe('CryptoMarketChart Canvas adapter', () => {
  it('normalizes candle values and forwards the local EMA and Donchian series', () => {
    const { html, canvas } = renderChart({ market: 'spot' })

    expect(canvas.bars).toEqual([
      { time: 0, open: 100, high: 110, low: 90, close: 105, volume: 2 },
      { time: 60, open: 105, high: 112, low: 101, close: 108, volume: 3 },
    ])
    expect(canvas.title).toBe('1m')
    expect(canvas.symbol).toBe('BTCUSDT')
    expect(canvas.contextKey).toBe('spot:BTCUSDT:1m')
    expect(canvas.series?.find(series => series.id === 'ema20')?.values[0]).toBe(105)
    expect(canvas.series?.find(series => series.id === 'ema60')?.values[0]).toBe(105)
    expect(canvas.series?.find(series => series.id === 'dc20-upper')?.values).toEqual([null, null])
    expect(html).not.toContain('TradingView')
    expect(html).not.toContain('Copyright')
  })

  it('forwards only market-matched trade markers and valid price levels', () => {
    const levels: CryptoMarketChartLevel[] = [
      { price: 95, label: '入场', color: '#38bdf8' },
      { price: -1, label: '无效价位', color: '#fff' },
      { price: 90, label: '   ', color: '#fff' },
    ]
    const { canvas } = renderChart({
      market: 'spot',
      trades: [
        { id: 'spot-buy', at_ms: 1_000, market: 'spot', symbol: 'BTCUSDT', action: 'buy' },
        { id: 'wrong-market', at_ms: 1_000, market: 'usdm', symbol: 'BTCUSDT', action: 'sell' },
        { id: 'wrong-symbol', at_ms: 1_000, market: 'spot', symbol: 'ETHUSDT', action: 'buy' },
        { id: 'unsupported-spot-action', at_ms: 1_000, market: 'spot', symbol: 'BTCUSDT', action: 'open_long' },
        { id: 'outside-loaded-range', at_ms: 180_000, market: 'spot', symbol: 'BTCUSDT', action: 'sell' },
      ],
      levels,
    })

    expect(canvas.markers).toEqual([{
      time: 0, position: 'below', shape: 'arrow', color: '#22c55e', label: '买',
    }])
    expect(canvas.priceLines).toEqual([{
      value: 95, label: '入场', color: '#38bdf8', dash: 'dashed',
    }])
  })

  it('keeps the Canvas context stable for data refreshes and changes it for interval switches', () => {
    const initial = renderChart()
    expect(initial.canvas.contextKey).toBe('usdm:BTCUSDT:1m')

    const refreshed = renderChart({ bars: testBars(120_000) })
    expect(refreshed.canvas.contextKey).toBe(initial.canvas.contextKey)

    const switched = renderChart({ bars: testBars(300_000), interval: '5m' })
    expect(switched.canvas.contextKey).toBe('usdm:BTCUSDT:5m')

    const empty = renderChart({ bars: [], interval: '5m' })
    expect(empty.canvas.bars).toEqual([])
    expect(empty.canvas.markers).toEqual([])
    expect(empty.canvas.series?.every(series => series.values.length === 0)).toBe(true)
  })
})
