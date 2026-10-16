// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { IntradayChart } from './DimensionMembersDialog'

const harness = vi.hoisted(() => ({
  init: vi.fn(),
  chart: { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() },
  theme: {} as Record<string, string>,
}))
vi.mock('echarts', () => ({ init: (...args: unknown[]) => harness.init(...args) }))
vi.mock('@/lib/theme', () => ({ useChartTheme: () => harness.theme }))

let host: HTMLDivElement
let root: Root
const darkTheme = {
  text: '#aaa', border: '#333', crosshair: '#777', tooltipBg: '#111',
  tooltipBorder: '#444', tooltipText: '#eee', grid: '#222',
}
const lightTheme = {
  text: '#222', border: '#ddd', crosshair: '#555', tooltipBg: '#fff',
  tooltipBorder: '#ccc', tooltipText: '#111', grid: '#eee',
}
const points = [
  { time: '09:30', sector: 0.0125, market: 0.002 },
  { time: '09:31', sector: null, market: -0.001 },
  { time: '09:32', sector: -0.004, market: null },
]

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  harness.init.mockReset().mockReturnValue(harness.chart)
  harness.chart.setOption.mockClear()
  harness.chart.resize.mockClear()
  harness.chart.dispose.mockClear()
  harness.theme = darkTheme
  host = document.createElement('div')
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  vi.unstubAllGlobals()
})

it('keeps aggregate percent series, null gaps, zero baseline, and theme palette in ECharts', async () => {
  await act(async () => root.render(<IntradayChart points={points} kind="industry" />))

  expect(harness.init).toHaveBeenCalledWith(host.querySelector('[role="img"]'), undefined, { renderer: 'canvas' })
  expect(host.querySelector('[role="img"]')?.getAttribute('aria-label')).toContain('百分比')
  const option = harness.chart.setOption.mock.calls.at(-1)?.[0] as any
  expect(option.xAxis.data).toEqual(['09:30', '09:31', '09:32'])
  expect(option.series[0].data).toEqual([0.0125, null, -0.004])
  expect(option.series[1].data).toEqual([0.002, -0.001, null])
  expect(option.series.every((series: any) => series.connectNulls === false)).toBe(true)
  expect(option.series[0].markLine.data).toEqual([{ yAxis: 0 }])
  expect(option.yAxis.min).toBeLessThan(0)
  expect(option.yAxis.max).toBeGreaterThan(0)
  expect(option.yAxis.axisLabel.formatter(0.0125)).toBe('+1.25%')
  expect(option.tooltip.formatter([
    { axisValueLabel: '09:30', seriesName: '板块', value: 0.0125 },
    { axisValueLabel: '09:30', seriesName: '全市场', value: 0.002 },
  ])).toContain('板块: +1.25%')
  expect(option.tooltip.backgroundColor).toBe('#111')

  harness.theme = lightTheme
  await act(async () => root.render(<IntradayChart points={points} kind="industry" />))
  const lightOption = harness.chart.setOption.mock.calls.at(-1)?.[0] as any
  expect(lightOption.xAxis.axisLabel.color).toBe('#222')
  expect(lightOption.tooltip.backgroundColor).toBe('#fff')

  await act(async () => root.unmount())
  expect(harness.chart.dispose).toHaveBeenCalledOnce()
  root = createRoot(host)
})
