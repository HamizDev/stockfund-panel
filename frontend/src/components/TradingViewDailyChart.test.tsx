// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { TradingViewDailyChart } from './TradingViewDailyChart'
import type { DailyKlineInput } from './tradingview-daily'

const mocked = vi.hoisted(() => {
  const makeSeries = () => ({
    applyOptions: vi.fn(),
    setData: vi.fn(),
    setMarkers: vi.fn(),
    createPriceLine: vi.fn(() => ({})),
    removePriceLine: vi.fn(),
    coordinateToPrice: vi.fn(() => 10.5),
  })
  const candle = makeSeries()
  const volume = makeSeries()
  const movingAverages = [makeSeries(), makeSeries(), makeSeries(), makeSeries()]
  const timeScale = {
    fitContent: vi.fn(),
    getVisibleLogicalRange: vi.fn<() => { from: number; to: number } | null>(() => null),
    setVisibleLogicalRange: vi.fn(),
  }
  const chart = {
    addCandlestickSeries: vi.fn(() => candle),
    addHistogramSeries: vi.fn(() => volume),
    addLineSeries: vi.fn()
      .mockReturnValueOnce(movingAverages[0])
      .mockReturnValueOnce(movingAverages[1])
      .mockReturnValueOnce(movingAverages[2])
      .mockReturnValueOnce(movingAverages[3]),
    applyOptions: vi.fn(),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    remove: vi.fn(),
    subscribeClick: vi.fn(),
    unsubscribeClick: vi.fn(),
    subscribeCrosshairMove: vi.fn(),
    unsubscribeCrosshairMove: vi.fn(),
    subscribeDblClick: vi.fn(),
    unsubscribeDblClick: vi.fn(),
    timeScale: vi.fn(() => timeScale),
  }
  return { candle, chart, movingAverages, timeScale, volume }
})

vi.mock('lightweight-charts', () => ({
  ColorType: { Solid: 0 },
  CrosshairMode: { Normal: 0 },
  LineStyle: { Dashed: 2, Dotted: 1 },
  createChart: vi.fn(() => mocked.chart),
}))

vi.mock('@/lib/theme', () => ({
  chartTheme: () => ({
    text: '#eeeeee',
    grid: '#333333',
    border: '#444444',
    crosshair: '#555555',
    crosshairLabelBg: '#222222',
  }),
  useTheme: () => 'dark',
}))

const testRows: DailyKlineInput[] = [
  { date: '2026-09-28', open: 10, high: 12, low: 9, close: 11, volume: null, ma5: 10.5 },
  { date: '2026-09-30', open: 11, high: 13, low: 10, close: 12, volume: 200, ma5: 11 },
]

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
let observerDisconnect: ReturnType<typeof vi.fn>

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  for (const series of [mocked.candle, mocked.volume, ...mocked.movingAverages]) {
    series.applyOptions.mockClear()
    series.setData.mockClear()
    series.setMarkers.mockClear()
    series.createPriceLine.mockClear()
    series.removePriceLine.mockClear()
    series.coordinateToPrice.mockClear().mockReturnValue(10.5)
  }
  mocked.chart.addLineSeries.mockReset()
    .mockReturnValueOnce(mocked.movingAverages[0])
    .mockReturnValueOnce(mocked.movingAverages[1])
    .mockReturnValueOnce(mocked.movingAverages[2])
    .mockReturnValueOnce(mocked.movingAverages[3])
  mocked.chart.addCandlestickSeries.mockClear()
  mocked.chart.addHistogramSeries.mockClear()
  mocked.chart.applyOptions.mockClear()
  mocked.chart.priceScale.mockClear()
  mocked.chart.remove.mockClear()
  mocked.chart.subscribeClick.mockClear()
  mocked.chart.unsubscribeClick.mockClear()
  mocked.chart.subscribeCrosshairMove.mockClear()
  mocked.chart.unsubscribeCrosshairMove.mockClear()
  mocked.chart.subscribeDblClick.mockClear()
  mocked.chart.unsubscribeDblClick.mockClear()
  mocked.chart.timeScale.mockClear()
  mocked.timeScale.fitContent.mockClear()
  mocked.timeScale.getVisibleLogicalRange.mockReset().mockReturnValue(null)
  mocked.timeScale.setVisibleLogicalRange.mockClear()
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

it('renders validated price data, omits invalid volume bars, and keeps TradingView attribution', async () => {
  await act(async () => root.render(
    <TradingViewDailyChart
      data={[...testRows, { ...testRows[1], date: '2026-10-01', volume: -1 }]}
      symbol="510300.SH"
      contextKey="510300.SH|2026-09-01|2026-09-30"
    />,
  ))

  expect(mocked.candle.setData).toHaveBeenCalledWith([
    { time: '2026-09-28', open: 10, high: 12, low: 9, close: 11 },
    { time: '2026-09-30', open: 11, high: 13, low: 10, close: 12 },
    { time: '2026-10-01', open: 11, high: 13, low: 10, close: 12 },
  ])
  expect(mocked.volume.setData).toHaveBeenCalledWith([
    { time: '2026-09-30', value: 200, color: 'rgba(199,64,64,0.48)' },
  ])
  expect(host.textContent).toContain('2 根有效价格K线缺少有效成交量')
  expect(host.textContent).toContain('开 11.00')
  expect(host.textContent).toContain('MA5 11.00')
  expect(host.textContent).toContain('NOTICE：TradingView Lightweight Charts™')
  expect(host.querySelector('a[href="https://www.tradingview.com/"]')).not.toBeNull()
  const { createChart } = await import('lightweight-charts')
  expect(vi.mocked(createChart).mock.calls.at(-1)?.[1]?.layout).toMatchObject({ attributionLogo: true })
})

it('does not attach a marker to a neighboring day when the exact date is absent', async () => {
  await act(async () => root.render(
    <TradingViewDailyChart
      data={testRows}
      symbol="510300.SH"
      contextKey="510300.SH|range"
      markers={[{ date: '2026-09-29', kind: 'buy', label: '买' }]}
    />,
  ))

  expect(mocked.candle.setMarkers).toHaveBeenCalledWith([])
})

it('forwards chart clicks by exact date and removes chart observers and listeners on cleanup', async () => {
  const onDateClick = vi.fn()
  const onPriceDoubleClick = vi.fn()
  await act(async () => root.render(
    <TradingViewDailyChart
      data={testRows}
      symbol="510300.SH"
      contextKey="510300.SH|range"
      onDateClick={onDateClick}
      onPriceDoubleClick={onPriceDoubleClick}
    />,
  ))

  const clickHandler = mocked.chart.subscribeClick.mock.calls.at(-1)?.[0] as (event: { time: unknown }) => void
  act(() => clickHandler({ time: { year: 2026, month: 9, day: 30 } }))
  expect(onDateClick).toHaveBeenCalledWith('2026-09-30')
  const doubleClickHandler = mocked.chart.subscribeDblClick.mock.calls.at(-1)?.[0] as (
    event: { point: { x: number; y: number } },
  ) => void
  act(() => doubleClickHandler({ point: { x: 50, y: 100 } }))
  expect(onPriceDoubleClick).toHaveBeenCalledWith(10.5, 12)

  await act(async () => root.unmount())
  expect(mocked.chart.unsubscribeClick).toHaveBeenCalledTimes(1)
  expect(mocked.chart.unsubscribeCrosshairMove).toHaveBeenCalledTimes(1)
  expect(mocked.chart.unsubscribeDblClick).toHaveBeenCalledTimes(1)
  expect(observerDisconnect).toHaveBeenCalledTimes(1)
  expect(mocked.chart.remove).toHaveBeenCalledTimes(1)
})
