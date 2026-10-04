// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CryptoMarketChart, type CryptoMarketBar } from './CryptoMarketChart'

const mocked = vi.hoisted(() => {
  const makeSeries = () => ({
    applyOptions: vi.fn(),
    setData: vi.fn(),
    setMarkers: vi.fn(),
    createPriceLine: vi.fn(() => ({})),
    removePriceLine: vi.fn(),
  })
  const candle = makeSeries()
  const volume = makeSeries()
  const ema20 = makeSeries()
  const ema60 = makeSeries()
  const timeScale = {
    fitContent: vi.fn(),
    getVisibleRange: vi.fn<() => { from: number; to: number } | null>(() => null),
    setVisibleRange: vi.fn(),
  }
  const chart = {
    addCandlestickSeries: vi.fn(() => candle),
    addHistogramSeries: vi.fn(() => volume),
    addLineSeries: vi.fn()
      .mockReturnValueOnce(ema20)
      .mockReturnValueOnce(ema60),
    applyOptions: vi.fn(),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    remove: vi.fn(),
    timeScale: vi.fn(() => timeScale),
  }
  return { candle, chart, ema20, ema60, timeScale, volume }
})

vi.mock('lightweight-charts', () => ({
  ColorType: { Solid: 0 },
  CrosshairMode: { Normal: 0 },
  LineStyle: { Dashed: 2 },
  createChart: vi.fn(() => mocked.chart),
}))

vi.mock('@/lib/theme', () => ({
  useChartTheme: () => ({
    text: '#eeeeee',
    grid: '#333333',
    border: '#444444',
    crosshair: '#555555',
    crosshairLabelBg: '#222222',
  }),
}))

const testBars = (endTime = 60_000): CryptoMarketBar[] => [
  { open_time_ms: 0, close_time_ms: 59_999, open: '100', high: '110', low: '90', close: '105', volume: '2' },
  { open_time_ms: endTime, close_time_ms: endTime + 59_999, open: '105', high: '112', low: '101', close: '108', volume: '3' },
]

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
let observerDisconnect: ReturnType<typeof vi.fn>

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  for (const series of [mocked.candle, mocked.volume, mocked.ema20, mocked.ema60]) {
    series.applyOptions.mockClear()
    series.setData.mockClear()
    series.setMarkers.mockClear()
    series.createPriceLine.mockClear()
    series.removePriceLine.mockClear()
  }
  mocked.chart.addLineSeries.mockClear()
  mocked.chart.addLineSeries.mockReturnValueOnce(mocked.ema20).mockReturnValueOnce(mocked.ema60)
  mocked.chart.addCandlestickSeries.mockClear()
  mocked.chart.addHistogramSeries.mockClear()
  mocked.chart.applyOptions.mockClear()
  mocked.chart.priceScale.mockClear()
  mocked.chart.remove.mockClear()
  mocked.chart.timeScale.mockClear()
  mocked.timeScale.fitContent.mockClear()
  mocked.timeScale.getVisibleRange.mockReset().mockReturnValue(null)
  mocked.timeScale.setVisibleRange.mockClear()
  observerDisconnect = vi.fn()
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() { observerDisconnect() }
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
})

it('renders validated candles, matched trade markers, levels and TradingView attribution', async () => {
  await act(async () => root.render(
    <CryptoMarketChart
      bars={testBars()}
      interval="1m"
      symbol="BTCUSDT"
      market="spot"
      trades={[{ id: 'spot-buy', at_ms: 1_000, market: 'spot', symbol: 'BTCUSDT', action: 'buy' }]}
      levels={[{ price: 95, label: '入场', color: '#38bdf8' }]}
    />,
  ))

  expect(mocked.candle.setData.mock.calls.at(-1)?.[0]).toHaveLength(2)
  expect(mocked.candle.setMarkers.mock.calls.at(-1)?.[0]).toMatchObject([
    { time: 0, text: '买', position: 'belowBar' },
  ])
  expect(mocked.candle.createPriceLine).toHaveBeenCalledWith(expect.objectContaining({ price: 95, title: '入场' }))
  expect(host.textContent).toContain('NOTICE')
  expect(host.textContent).toContain('Copyright (с) 2024 TradingView, Inc.')
  expect(host.querySelector('a[href="https://www.tradingview.com/"]')).not.toBeNull()

  const { createChart } = await import('lightweight-charts')
  expect(vi.mocked(createChart).mock.calls[0]?.[1]?.layout).toMatchObject({ attributionLogo: true })
})

it('preserves same-context zoom, fits after context switches, and clears all series for empty data', async () => {
  const render = async (symbol: string, interval: '1m' | '5m', bars: CryptoMarketBar[]) => {
    await act(async () => root.render(<CryptoMarketChart bars={bars} interval={interval} symbol={symbol} />))
  }

  await render('BTCUSDT', '1m', testBars())
  expect(mocked.timeScale.fitContent).toHaveBeenCalledTimes(1)

  const visibleRange = { from: 0, to: 60 }
  mocked.timeScale.getVisibleRange.mockReturnValue(visibleRange)
  await render('BTCUSDT', '1m', testBars(120_000))
  expect(mocked.timeScale.setVisibleRange).toHaveBeenCalledWith(visibleRange)
  expect(mocked.timeScale.fitContent).toHaveBeenCalledTimes(1)

  await render('BTCUSDT', '5m', testBars(300_000))
  expect(mocked.timeScale.fitContent).toHaveBeenCalledTimes(2)

  await render('BTCUSDT', '5m', [])
  expect(mocked.candle.setData.mock.calls.at(-1)?.[0]).toEqual([])
  expect(mocked.volume.setData.mock.calls.at(-1)?.[0]).toEqual([])
  expect(mocked.ema20.setData.mock.calls.at(-1)?.[0]).toEqual([])
  expect(mocked.ema60.setData.mock.calls.at(-1)?.[0]).toEqual([])
  expect(mocked.candle.setMarkers.mock.calls.at(-1)?.[0]).toEqual([])
})

it('disconnects the resize observer and removes the chart on unmount', async () => {
  await act(async () => root.render(<CryptoMarketChart bars={testBars()} interval="1m" symbol="BTCUSDT" />))
  await act(async () => root.unmount())

  expect(observerDisconnect).toHaveBeenCalledOnce()
  expect(mocked.chart.remove).toHaveBeenCalledOnce()
})
