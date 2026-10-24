// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StockDailyKChart } from './StockDailyKChart'

const mocked = vi.hoisted(() => {
  const values = new Map<string, unknown>()
  const preference = (key: string) => ({
    get: vi.fn((fallback: unknown) => values.has(key) ? values.get(key) : fallback),
    set: vi.fn((value: unknown) => { values.set(key, value) }),
  })
  return {
    useQuery: vi.fn(),
    values,
    clearPreferences: () => values.clear(),
    storage: {
      stockVolumeCompare: { get: vi.fn(() => ({ enabled: true, days: 1 })), set: vi.fn() },
      stockChartIndicators: preference('indicators'),
      stockChartMode: preference('mode'),
    },
  }
})

vi.mock('@tanstack/react-query', () => ({ useQuery: mocked.useQuery }))
vi.mock('@/lib/kline', () => ({
  klineDailyQueryOptions: vi.fn(() => ({ queryKey: ['daily'], queryFn: vi.fn() })),
}))
vi.mock('@/lib/storage', () => ({
  storage: mocked.storage,
}))
vi.mock('@/components/EChartsCandlestick', async () => {
  const React = await import('react')
  return {
    EChartsCandlestick: (props: { activeIndicators: string[] }) => React.createElement('div', {
      'data-testid': 'professional-chart',
      'data-active-indicators': JSON.stringify(props.activeIndicators),
    }),
    OVERLAY_INDICATORS: [{ key: 'boll', label: 'BOLL' }],
    SUB_CHARTS: [
      { key: 'adx', label: 'ADX 趋势', height: 84 },
      { key: 'vol', label: '成交量', height: 84 },
      { key: 'macd', label: 'MACD', height: 84 },
      { key: 'rsi', label: 'RSI', height: 84 },
      { key: 'kdj', label: 'KDJ', height: 84 },
    ],
  }
})
vi.mock('@/components/TradingViewDailyChart', async () => {
  const React = await import('react')
  return {
    TradingViewDailyChart: (props: { contextKey: string }) => React.createElement(
      'div',
      { 'data-testid': 'canvas-chart', 'data-context': props.contextKey },
    ),
  }
})

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

beforeEach(() => {
  mocked.clearPreferences()
  mocked.storage.stockChartIndicators.set.mockClear()
  mocked.storage.stockChartMode.set.mockClear()
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  mocked.useQuery.mockReturnValue({
    data: {
      rows: [{
        date: '2026-09-30',
        open: 10,
        high: 12,
        low: 9,
        close: 11,
        volume: 100,
      }],
    },
    isLoading: false,
    isError: false,
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  mocked.useQuery.mockReset()
})

it('defaults to the self-built Canvas chart and can switch to the professional ECharts chart', async () => {
  await act(async () => root.render(
    <StockDailyKChart symbol="510300.SH" dateRange={{ start: '2026-09-01', end: '2026-09-30' }} />,
  ))

  expect(host.querySelector('[data-testid="canvas-chart"]')?.getAttribute('data-context'))
    .toBe('510300.SH|2026-09-01|2026-09-30')
  expect(host.querySelector('[data-testid="professional-chart"]')).toBeNull()
  expect(mocked.values.get('indicators')).toEqual(['vol'])

  const canvasButton = [...host.querySelectorAll('button')].find(button => button.textContent === '自研Canvas')
  expect(canvasButton?.getAttribute('aria-pressed')).toBe('true')
  const professionalButton = [...host.querySelectorAll('button')].find(button => button.textContent === '专业指标')
  expect(professionalButton).toBeDefined()
  act(() => professionalButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  expect(host.querySelector('[data-testid="professional-chart"]')).not.toBeNull()
  expect(host.querySelector('[data-testid="professional-chart"]')?.getAttribute('data-active-indicators'))
    .toBe('["vol"]')
  expect(host.querySelector('[data-testid="canvas-chart"]')).toBeNull()

  const macdButton = [...host.querySelectorAll('button')].find(button => button.textContent === 'MACD')
  const bollButton = [...host.querySelectorAll('button')].find(button => button.textContent === 'BOLL')
  act(() => macdButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  act(() => bollButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  expect(mocked.values.get('indicators')).toEqual(['vol', 'macd', 'boll'])
  expect(mocked.values.get('mode')).toBe('professional')

  await act(async () => root.render(
    <StockDailyKChart symbol="600000.SH" dateRange={{ start: '2026-09-01', end: '2026-09-30' }} />,
  ))
  expect(host.querySelector('[data-testid="professional-chart"]')?.getAttribute('data-active-indicators'))
    .toBe('["vol","macd","boll"]')

  await act(async () => root.unmount())
  root = createRoot(host)
  await act(async () => root.render(
    <StockDailyKChart symbol="600000.SH" dateRange={{ start: '2026-09-01', end: '2026-09-30' }} />,
  ))
  expect(host.querySelector('[data-testid="professional-chart"]')?.getAttribute('data-active-indicators'))
    .toBe('["vol","macd","boll"]')
})

it('defaults to professional mode when backtest ranges must remain visible', async () => {
  await act(async () => root.render(
    <StockDailyKChart
      symbol="510300.SH"
      dateRange={{ start: '2026-09-01', end: '2026-09-30' }}
      ranges={[{ start: '2026-09-28', end: '2026-09-30', label: '持仓区间' }]}
    />,
  ))

  expect(host.querySelector('[data-testid="professional-chart"]')).not.toBeNull()
  expect(host.querySelector('[data-testid="canvas-chart"]')).toBeNull()
  expect(host.textContent).toContain('回测区间标注仅在专业指标图中完整显示')
  const canvasButton = [...host.querySelectorAll('button')].find(button => button.textContent === '自研Canvas')
  expect(canvasButton?.disabled).toBe(true)
  expect(canvasButton?.getAttribute('aria-pressed')).toBe('false')
  const professionalButton = [...host.querySelectorAll('button')].find(button => button.textContent === '专业指标')
  expect(professionalButton?.getAttribute('aria-pressed')).toBe('true')
  expect(mocked.values.get('mode')).toBeUndefined()
  expect(mocked.storage.stockChartMode.set).not.toHaveBeenCalledWith('professional')

  await act(async () => root.render(
    <StockDailyKChart symbol="510300.SH" dateRange={{ start: '2026-09-01', end: '2026-09-30' }} />,
  ))
  expect(host.querySelector('[data-testid="canvas-chart"]')).not.toBeNull()
  expect(mocked.values.get('mode')).toBeUndefined()
})

it('defaults malformed preferences and a nonempty invalid indicator list to Canvas and volume', async () => {
  mocked.values.set('indicators', { unknown: true })
  mocked.values.set('mode', 'tradingview')
  await act(async () => root.render(
    <StockDailyKChart symbol="510300.SH" dateRange={{ start: '2026-09-01', end: '2026-09-30' }} />,
  ))
  expect(host.querySelector('[data-testid="canvas-chart"]')).not.toBeNull()

  const professionalButton = [...host.querySelectorAll('button')].find(button => button.textContent === '专业指标')
  act(() => professionalButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  expect(host.querySelector('[data-testid="professional-chart"]')?.getAttribute('data-active-indicators'))
    .toBe('["vol"]')
  expect(mocked.values.get('indicators')).toEqual(['vol'])

  await act(async () => root.unmount())
  mocked.values.set('indicators', ['unknown', null, 4])
  root = createRoot(host)
  await act(async () => root.render(
    <StockDailyKChart symbol="510300.SH" dateRange={{ start: '2026-09-01', end: '2026-09-30' }} />,
  ))
  const reopenedProfessionalButton = [...host.querySelectorAll('button')].find(button => button.textContent === '专业指标')
  act(() => reopenedProfessionalButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  expect(host.querySelector('[data-testid="professional-chart"]')?.getAttribute('data-active-indicators'))
    .toBe('["vol"]')
})

it('keeps only unique real indicator keys and treats an explicit empty list as all off', async () => {
  mocked.values.set('indicators', ['boll', 'macd', 'not-an-indicator', 'macd', ...Array(200).fill('vol')])
  await act(async () => root.render(
    <StockDailyKChart symbol="510300.SH" dateRange={{ start: '2026-09-01', end: '2026-09-30' }} />,
  ))
  const professionalButton = [...host.querySelectorAll('button')].find(button => button.textContent === '专业指标')
  act(() => professionalButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  expect(host.querySelector('[data-testid="professional-chart"]')?.getAttribute('data-active-indicators'))
    .toBe('["boll","macd","vol"]')
  expect(mocked.values.get('indicators')).toEqual(['boll', 'macd', 'vol'])

  for (const label of ['BOLL', 'MACD', '成交量']) {
    const button = [...host.querySelectorAll('button')].find(candidate => candidate.textContent === label)
    act(() => button?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  }
  expect(mocked.values.get('indicators')).toEqual([])
  await act(async () => root.unmount())
  root = createRoot(host)
  await act(async () => root.render(
    <StockDailyKChart symbol="510300.SH" dateRange={{ start: '2026-09-01', end: '2026-09-30' }} />,
  ))
  expect(host.querySelector('[data-testid="professional-chart"]')?.getAttribute('data-active-indicators')).toBe('[]')
  expect(mocked.values.get('indicators')).toEqual([])
})

it('persists an explicit Canvas choice after switching from a remembered professional mode', async () => {
  mocked.values.set('mode', 'professional')
  await act(async () => root.render(
    <StockDailyKChart symbol="510300.SH" dateRange={{ start: '2026-09-01', end: '2026-09-30' }} />,
  ))
  expect(host.querySelector('[data-testid="professional-chart"]')).not.toBeNull()

  const canvasButton = [...host.querySelectorAll('button')].find(button => button.textContent === '自研Canvas')
  act(() => canvasButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  expect(mocked.values.get('mode')).toBe('canvas')

  await act(async () => root.unmount())
  root = createRoot(host)
  await act(async () => root.render(
    <StockDailyKChart symbol="510300.SH" dateRange={{ start: '2026-09-01', end: '2026-09-30' }} />,
  ))
  expect(host.querySelector('[data-testid="canvas-chart"]')).not.toBeNull()
})
