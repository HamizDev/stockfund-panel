// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AnalysisKChart } from './AnalysisKChart'
import { dateRangeToIndexRange, indexRangeToDateRange, indexRangeToZoomRange, presetToIndexRange } from './chartRange'

const mock = vi.hoisted(() => ({
  handlers: new Map<string, (event?: any) => void>(),
  init: vi.fn(),
  option: undefined as any,
  chart: undefined as any,
}))

vi.mock('echarts', () => ({ init: mock.init }))
vi.mock('@/components/DatePicker', async () => {
  const React = await import('react')
  return {
    DatePicker: ({ value, onChange, placeholder, min, max }: any) => React.createElement('input', {
      type: 'date', value, min, max, 'aria-label': placeholder,
      onChange: (event: any) => onChange(event.target.value),
    }),
  }
})

let host: HTMLDivElement
let root: Root
let rows: { date: string; open: number; high: number; low: number; close: number; volume: number }[]

function makeRows(length: number) {
  const last = Date.UTC(2026, 9, 16)
  return Array.from({ length }, (_, index) => {
    const date = new Date(last - (length - index - 1) * 86_400_000).toISOString().slice(0, 10)
    return { date, open: 1, high: 1.2, low: 0.9, close: 1.1, volume: 100 }
  })
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  rows = makeRows(400)
  sessionStorage.clear()
  mock.handlers.clear()
  mock.option = undefined
  const chart = {
    on: vi.fn((name: string, callback: (event?: any) => void) => mock.handlers.set(name, callback)),
    off: vi.fn(),
    getOption: vi.fn(() => mock.option),
    setOption: vi.fn((option: any) => { mock.option = option }),
    resize: vi.fn(),
    dispose: vi.fn(),
    dispatchAction: vi.fn((action: any) => {
      if (!mock.option?.dataZoom) return
      mock.option.dataZoom = mock.option.dataZoom.map((zoom: any) => ({ ...zoom, start: action.start, end: action.end }))
      mock.handlers.get('dataZoom')?.()
    }),
  }
  mock.chart = chart
  mock.init.mockReturnValue(chart)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  sessionStorage.clear()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

async function render(chartRows = rows, key = 'symbol-a', persistenceKey?: string) {
  const levels = chartRows.length ? {
    sr: [{ value: 1.2, label: '压力位', type: 'sr', side: 'resistance' }],
    pivot: [{ value: 1.1, label: '枢轴 P', type: 'pivot', side: 'neutral', rank: 0 }],
  } as any : undefined
  await act(async () => root.render(<AnalysisKChart key={key} rows={chartRows} levels={levels} persistenceKey={persistenceKey} />))
}

async function clickButton(label: string) {
  const button = [...host.querySelectorAll('button')].find(item => item.getAttribute('aria-label') === label || item.textContent?.trim() === label)
  if (!button) throw new Error(`Button not found: ${label}`)
  await act(async () => button.click())
}

it('applies a shortcut to the chart and keeps the whole loaded dataset available', async () => {
  await render()
  const all = host.querySelector<HTMLButtonElement>('button[aria-label="全部已加载日K"]')!
  const allIndices = presetToIndexRange(rows.map(row => row.date), 'all')!
  const allDates = indexRangeToDateRange(rows.map(row => row.date), allIndices)!

  await act(async () => all.click())

  expect(all.getAttribute('aria-pressed')).toBe('true')
  expect(sessionStorage.getItem('analysis-k-chart:view-state:v1')).toBeNull()
  expect(mock.chart.dispatchAction).toHaveBeenLastCalledWith({
    type: 'dataZoom', ...indexRangeToZoomRange(allIndices, rows.length),
  })
  expect(host.textContent).toContain(`当前显示：${allDates.start} ~ ${allDates.end} · 400/400 根`)
  expect(host.textContent).toContain('日K全部指已加载数据')
})

it('resizes after its container changes width without rebuilding data or resetting zoom', async () => {
  let resize!: () => void
  const observe = vi.fn()
  const disconnect = vi.fn()
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { resize = callback }
    observe = observe
    disconnect = disconnect
  })
  await render()
  expect(observe).toHaveBeenCalledOnce()
  const calls = mock.chart.setOption.mock.calls.length
  resize()
  expect(mock.chart.resize).toHaveBeenCalledOnce()
  expect(mock.chart.setOption).toHaveBeenCalledTimes(calls)
  await act(async () => root.unmount())
  expect(disconnect).toHaveBeenCalledOnce()
  root = createRoot(host)
})

it('updates only the linked price guide and clears invalid prices without resetting zoom', async () => {
  const renderLinked = async (linkedPrice: number | null) => {
    await act(async () => root.render(<AnalysisKChart rows={rows} linkedPrice={linkedPrice} />))
  }
  await renderLinked(null)
  const initialCalls = mock.chart.setOption.mock.calls.length
  await renderLinked(.521)
  expect(mock.chart.setOption).toHaveBeenCalledTimes(initialCalls + 1)
  const [patch, settings] = mock.chart.setOption.mock.calls.at(-1)
  expect(settings).toEqual({ lazyUpdate: true })
  expect(patch.dataZoom).toBeUndefined()
  expect(patch.series[0].id).toBe('analysis-candles')
  expect(patch.series[0].data).toBeUndefined()
  const guide = patch.series[0].markLine.data[0]
  expect(guide.yAxis).toBe(.521)
  expect(guide.label.formatter).toContain('0.521')
  await renderLinked(Number.NaN)
  expect(mock.chart.setOption.mock.calls.at(-1)[0].series[0].markLine.data).toEqual([])
})

it('syncs explicit dates and manual slider changes, then preserves a drag across hover redraws', async () => {
  await render()
  const startInput = host.querySelector<HTMLInputElement>('input[aria-label="起始日期"]')!
  const selectedStart = rows[250].date
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(startInput, selectedStart)
    startInput.dispatchEvent(new Event('input', { bubbles: true }))
  })

  const explicitIndices = dateRangeToIndexRange(rows.map(row => row.date), {
    start: selectedStart,
    end: rows[rows.length - 1].date,
  })!
  expect(host.textContent).toContain(`当前显示：${rows[explicitIndices.start].date} ~ ${rows[explicitIndices.end].date}`)
  expect(mock.chart.dispatchAction).toHaveBeenLastCalledWith({
    type: 'dataZoom', ...indexRangeToZoomRange(explicitIndices, rows.length),
  })

  const dragged = { start: 30, end: 60 }
  await act(async () => {
    mock.option.dataZoom = mock.option.dataZoom.map((zoom: any) => ({ ...zoom, ...dragged }))
    mock.handlers.get('dataZoom')?.()
  })
  const draggedIndices = { start: 120, end: 239 }
  expect(host.textContent).toContain(`当前显示：${rows[120].date} ~ ${rows[239].date} · 120/400 根`)
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="最近六个月"]')?.getAttribute('aria-pressed')).toBe('false')

  await act(async () => mock.handlers.get('mouseover')?.({ componentType: 'series', seriesIndex: 2 }))
  const expectedZoom = indexRangeToZoomRange(draggedIndices, rows.length)
  expect(mock.option.dataZoom[0].start).toBe(expectedZoom.start)
  expect(mock.option.dataZoom[0].end).toBe(expectedZoom.end)
  expect(mock.option.dataZoom[1].start).toBe(expectedZoom.start)
  expect(mock.option.dataZoom[1].end).toBe(expectedZoom.end)
})

it('restores all level lines when leaving a level for candles or chart background', async () => {
  await render()
  const opacity = () => mock.option.series.slice(2).map((line: any) => line.lineStyle.opacity)
  expect(opacity()).toEqual([.7, .7])
  await act(async () => mock.handlers.get('mouseover')?.({ componentType: 'series', seriesIndex: 2 }))
  expect(opacity()).toEqual([1, .12])
  await act(async () => mock.handlers.get('mouseover')?.({ componentType: 'series', seriesIndex: 0 }))
  expect(opacity()).toEqual([.7, .7])
  await act(async () => mock.handlers.get('mouseover')?.({ componentType: 'series', seriesIndex: 2 }))
  await act(async () => mock.handlers.get('mouseout')?.({ componentType: 'series', seriesIndex: 2 }))
  expect(opacity()).toEqual([.7, .7])
})

it('keeps a selected historical range visible until the user jumps to and follows the latest K-line', async () => {
  await render()
  const endInput = host.querySelector<HTMLInputElement>('input[aria-label="结束日期"]')!
  const historicalEnd = rows[250].date
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(endInput, historicalEnd)
    endInput.dispatchEvent(new Event('input', { bubbles: true }))
  })

  expect(host.textContent).toContain(`~ ${rows[250].date}`)
  expect(host.textContent).toContain(`当前范围未显示最新日K（${rows.at(-1)!.date}）`)
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="最新K线"]')).not.toBeNull()

  await clickButton('最新K线')
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="最近六个月"]')?.getAttribute('aria-pressed')).toBe('true')
  expect(host.textContent).not.toContain('当前范围未显示最新日K')
  expect(host.textContent).toContain(`~ ${rows.at(-1)!.date}`)

  const latestBar = { date: '2026-10-22', open: 1.1, high: 1.2, low: 1, close: 1.15, volume: 140 }
  await render([...rows, latestBar])
  expect(mock.option.xAxis[0].data).toContain('2026-10-22')
  expect(host.textContent).not.toContain('当前范围未显示最新日K')
  expect(host.textContent).toContain('~ 2026-10-22')
})

it('restores the compact view for the same key and replaces it when the symbol key changes', async () => {
  await render(rows, 'same-component', 'symbol-a')
  await clickButton('近1月')
  expect(JSON.parse(sessionStorage.getItem('analysis-k-chart:view-state:v1')!).activeRangePreset).toBe('1m')

  await act(async () => root.render(null))
  await render(rows, 'same-component', 'symbol-a')
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="最近一个月"]')?.getAttribute('aria-pressed')).toBe('true')

  const startInput = host.querySelector<HTMLInputElement>('input[aria-label="起始日期"]')!
  const customStart = rows[350].date
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(startInput, customStart)
    startInput.dispatchEvent(new Event('input', { bubbles: true }))
  })

  const srButton = [...host.querySelectorAll('button')].find(button => button.textContent?.startsWith('压力支撑'))!
  await act(async () => srButton.click())
  const rankThree = host.querySelector<HTMLButtonElement>('button[title="全档 R3/S3(7 个)"]')!
  await act(async () => rankThree.click())

  const stored = JSON.parse(sessionStorage.getItem('analysis-k-chart:view-state:v1')!)
  expect(stored).toMatchObject({
    persistenceKey: 'symbol-a',
    pivotRank: 3,
    activeRangePreset: null,
    visibleDateRange: { start: customStart, end: rows[rows.length - 1].date },
  })
  expect(Object.keys(stored).sort()).toEqual([
    'activeRangePreset', 'activeTypes', 'persistenceKey', 'pivotRank', 'version', 'visibleDateRange',
  ])

  await act(async () => root.render(null))
  await render(rows, 'remounted-component', 'symbol-a')
  expect(host.querySelector<HTMLInputElement>('input[aria-label="起始日期"]')?.value).toBe(customStart)
  expect([...host.querySelectorAll('button')].find(button => button.textContent?.startsWith('压力支撑'))?.style.borderColor).toBe('')
  expect(host.querySelector<HTMLButtonElement>('button[title="全档 R3/S3(7 个)"]')?.className).toContain('bg-[#8B5CF6]/15')

  // Keep the React component mounted: changing the persistence key must reset and replace the single stored slot.
  await render(rows, 'remounted-component', 'symbol-b')
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="最近六个月"]')?.getAttribute('aria-pressed')).toBe('true')
  expect(host.querySelector<HTMLInputElement>('input[aria-label="起始日期"]')?.value).not.toBe(customStart)
  expect([...host.querySelectorAll('button')].find(button => button.textContent?.startsWith('压力支撑'))?.style.borderColor).not.toBe('')
  expect(host.querySelector<HTMLButtonElement>('button[title="全档 R3/S3(7 个)"]')?.className).not.toContain('bg-[#8B5CF6]/15')
  expect(JSON.parse(sessionStorage.getItem('analysis-k-chart:view-state:v1')!).persistenceKey).toBe('symbol-b')

  await render(rows, 'remounted-component', 'symbol-a')
  expect(host.querySelector<HTMLInputElement>('input[aria-label="起始日期"]')?.value).not.toBe(customStart)
  expect([...host.querySelectorAll('button')].find(button => button.textContent?.startsWith('压力支撑'))?.style.borderColor).not.toBe('')
  expect(JSON.parse(sessionStorage.getItem('analysis-k-chart:view-state:v1')!).persistenceKey).toBe('symbol-a')
})

it('disables range controls for empty and single-bar data, and resets on a keyed symbol switch', async () => {
  await render([])
  expect(host.querySelector('[role="status"]')?.textContent).toBe('暂无已加载日K')
  expect([...host.querySelectorAll('button[aria-label="全部已加载日K"]')][0].hasAttribute('disabled')).toBe(true)

  await render([rows[0]])
  expect(host.querySelector('[role="status"]')?.textContent).toContain('仅 1 根已加载日K')
  expect(host.querySelector<HTMLInputElement>('input[aria-label="起始日期"]')).toBeNull()

  await render(rows, 'symbol-a')
  await clickButton('近1月')
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="最近一个月"]')?.getAttribute('aria-pressed')).toBe('true')

  await render(rows, 'symbol-b')
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="最近六个月"]')?.getAttribute('aria-pressed')).toBe('true')
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="最近一个月"]')?.getAttribute('aria-pressed')).toBe('false')
})
