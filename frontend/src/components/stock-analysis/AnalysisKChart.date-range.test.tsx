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
  vi.clearAllMocks()
})

async function render(chartRows = rows, key = 'symbol-a') {
  const levels = chartRows.length ? {
    sr: [{ value: 1.2, label: '压力位', type: 'sr', side: 'resistance' }],
  } as any : undefined
  await act(async () => root.render(<AnalysisKChart key={key} rows={chartRows} levels={levels} />))
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
  expect(mock.chart.dispatchAction).toHaveBeenLastCalledWith({
    type: 'dataZoom', ...indexRangeToZoomRange(allIndices, rows.length),
  })
  expect(host.textContent).toContain(`当前显示：${allDates.start} ~ ${allDates.end} · 400/400 根`)
  expect(host.textContent).toContain('日K全部指已加载数据')
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
