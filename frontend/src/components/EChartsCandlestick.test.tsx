// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { EChartsCandlestick, type OHLC } from './EChartsCandlestick'

const chart = vi.hoisted(() => ({
  handlers: {} as Record<string, (event?: any) => void>,
  state: { zoomRange: { start: 0, end: 100 } },
  setOption: vi.fn(),
  on: vi.fn(),
  off: vi.fn(),
  dispatchAction: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  getZr: () => ({ on: vi.fn(), off: vi.fn() }),
  getOption: () => ({ dataZoom: [chart.state.zoomRange] }),
  containPixel: () => false,
  convertFromPixel: () => [0, 0],
}))

vi.mock('echarts', () => ({ init: () => chart }))

// 超过 COMPACT_THRESHOLD(60): 切标的后 dataZoom 一响就会走紧凑态转换, 进而重写 markPoint
const DAYS = Array.from({ length: 62 }, (_, i) =>
  new Date(Date.UTC(2024, 0, 1 + i)).toISOString().slice(0, 10))
const rows = (base: number): OHLC[] =>
  DAYS.map(date => ({ date, open: base, high: base + 1, low: base - 1, close: base, volume: 1 }))

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  chart.handlers = {}
  chart.setOption.mockClear()
  chart.dispatchAction.mockClear()
  chart.on.mockImplementation((name: string, callback: (event?: any) => void) => {
    chart.handlers[name] = callback
  })
  chart.dispatchAction.mockImplementation((action: any) => {
    if (action?.type === 'dataZoom') {
      chart.state.zoomRange = { start: action.start, end: action.end }
    }
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

/** 最后一次 setOption 里 K 线的 markPoint (买卖箭头来源) */
function lastMarkPoint(): string {
  const option = chart.setOption.mock.calls.at(-1)?.[0] as any
  const k = option?.series?.find((s: any) => s.name === 'K')
  return JSON.stringify(k?.markPoint ?? null)
}

/** 模拟竖虚线命中某根 K 线 (鼠标在数据区内移动时 echarts 派发的事件) */
async function hoverCandle(index: number) {
  await act(async () => { chart.handlers.updateAxisPointer?.({ axesInfo: [{ value: index }] }) })
}

it('切标的后响应 dataZoom 仍用当前标的的买卖标记, 不回退到上一只', async () => {
  const render = async (symbol: string, data: OHLC[], markerLabel: string, markerDate: string) => {
    await act(async () => root.render(
      <EChartsCandlestick
        data={data}
        markers={[{ date: markerDate, kind: 'buy', label: markerLabel }]}
        symbol={symbol}
        height={400}
        showInfoBar={false}
        visibleBars="all"
        activeIndicators={['vol']}
      />,
    ))
  }

  await render('600000', rows(10), 'A-BUY', DAYS[10])
  await render('000001', rows(100), 'B-SELL', DAYS[20])

  // 切换标的后图表实例被复用, dataZoom 监听仍是最初注册的那个; 首次 setOption 已是当前标的
  expect(lastMarkPoint()).toContain('B-SELL')

  await act(async () => { chart.handlers.dataZoom?.() })

  expect(lastMarkPoint()).toContain('B-SELL')
  expect(lastMarkPoint()).not.toContain('A-BUY')
})

it('切股后鼠标未离开图表, 竖虚线重新命中即恢复「至今/周期」', async () => {
  const render = async (symbol: string) => {
    await act(async () => root.render(
      <EChartsCandlestick data={rows(10)} symbol={symbol} height={400} visibleBars="all" />,
    ))
  }

  await render('600000')
  const surface = host.firstElementChild as HTMLElement
  await act(async () => { surface.dispatchEvent(new MouseEvent('mouseenter')) })
  await hoverCandle(30)
  expect(host.textContent).toContain('至今')

  // 切股: 上一只的悬停上下文作废, 清掉「至今/周期」
  await render('000001')
  expect(host.textContent).not.toContain('至今')

  // 鼠标没离开图表区 (只是切股), 竖虚线重新命中即恢复, 不靠 mouseenter
  await hoverCandle(20)
  expect(host.textContent).toContain('至今')
  expect(host.textContent).toContain('周期')

  // 移出图表区 → 重新收起
  await act(async () => { surface.dispatchEvent(new MouseEvent('mouseleave')) })
  expect(host.textContent).not.toContain('至今')
})

it('联动价只更新K线价线, 不重建主图或重派缩放, 并保留其他价线和标记', async () => {
  const marker = { date: DAYS[10], kind: 'buy' as const, label: 'BUY-MARK' }
  const markers = [marker]
  const priceLines = [{ value: 12, label: '支撑', color: '#38bdf8' }]
  const baseData = rows(10)
  const render = async (linkedPrice: number | null, data = baseData) => act(async () => root.render(
    <EChartsCandlestick
      data={data}
      markers={markers}
      priceLines={priceLines}
      linkedPrice={linkedPrice}
      height={400}
      showInfoBar={false}
      visibleBars="all"
      activeIndicators={['vol']}
    />,
  ))
  const fullOptions = () => chart.setOption.mock.calls.filter(([, replace]) => replace === true)
  const candleSeries = (option: any) => option?.series?.find((series: any) =>
    series.id === 'daily-candles' || series.name === 'K')

  await render(null)
  const initialFullOptionCount = fullOptions().length
  const initialOption = fullOptions().at(-1)?.[0]
  expect(candleSeries(initialOption)?.markLine.data).toHaveLength(1)
  expect(JSON.stringify(candleSeries(initialOption)?.markPoint)).toContain('BUY-MARK')

  // Simulate a user-selected zoom and let the chart's normal handler remember it.
  chart.state.zoomRange = { start: 35, end: 72 }
  await act(async () => { chart.handlers.dataZoom?.() })
  const userZoom = { ...chart.state.zoomRange }
  const zoomActionsBeforeHover = chart.dispatchAction.mock.calls.filter(([action]) => action?.type === 'dataZoom').length

  await render(10.5)
  expect(fullOptions()).toHaveLength(initialFullOptionCount)
  expect(chart.dispatchAction.mock.calls.filter(([action]) => action?.type === 'dataZoom')).toHaveLength(zoomActionsBeforeHover)
  expect(chart.state.zoomRange).toEqual(userZoom)
  const linkedPatch = chart.setOption.mock.calls.at(-1)
  expect(linkedPatch?.[1]).toEqual({ lazyUpdate: true })
  expect(linkedPatch?.[0].series).toHaveLength(1)
  expect(linkedPatch?.[0].series[0]).toMatchObject({ id: 'daily-candles' })
  expect(linkedPatch?.[0].series[0].data).toBeUndefined()
  expect(linkedPatch?.[0].series[0].markLine.data).toHaveLength(2)
  expect(linkedPatch?.[0].series[0].markLine.data[0].yAxis).toBe(12)
  expect(linkedPatch?.[0].series[0].markLine.data[1].yAxis).toBe(10.5)
  expect(JSON.stringify(linkedPatch?.[0].series[0].markLine.data[0])).toContain('支撑')

  await render(null)
  const clearPatch = chart.setOption.mock.calls.at(-1)
  expect(fullOptions()).toHaveLength(initialFullOptionCount)
  expect(clearPatch?.[0].series[0].markLine.data).toHaveLength(1)
  expect(clearPatch?.[0].series[0].markLine.data[0].yAxis).toBe(12)

  // A normal data change still rebuilds the chart, including the currently linked guide and marker.
  await render(10.8, rows(20))
  const rebuiltOption = fullOptions().at(-1)?.[0]
  expect(fullOptions()).toHaveLength(initialFullOptionCount + 1)
  expect(candleSeries(rebuiltOption)?.markLine.data.map((item: any) => item.yAxis)).toEqual([12, 10.8])
  expect(JSON.stringify(candleSeries(rebuiltOption)?.markPoint)).toContain('BUY-MARK')
})
