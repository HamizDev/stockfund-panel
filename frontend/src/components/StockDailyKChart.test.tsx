// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StockDailyKChart } from './StockDailyKChart'

const mocked = vi.hoisted(() => ({
  useQuery: vi.fn(),
}))

vi.mock('@tanstack/react-query', () => ({ useQuery: mocked.useQuery }))
vi.mock('@/lib/kline', () => ({
  klineDailyQueryOptions: vi.fn(() => ({ queryKey: ['daily'], queryFn: vi.fn() })),
}))
vi.mock('@/lib/storage', () => ({
  storage: { stockVolumeCompare: { get: () => ({ enabled: true, days: 1 }), set: vi.fn() } },
}))
vi.mock('@/components/EChartsCandlestick', async () => {
  const React = await import('react')
  return {
    EChartsCandlestick: () => React.createElement('div', { 'data-testid': 'professional-chart' }),
    OVERLAY_INDICATORS: [],
    SUB_CHARTS: [],
  }
})
vi.mock('@/components/TradingViewDailyChart', async () => {
  const React = await import('react')
  return {
    TradingViewDailyChart: (props: { contextKey: string }) => React.createElement(
      'div',
      { 'data-testid': 'tradingview-chart', 'data-context': props.contextKey },
    ),
  }
})

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

beforeEach(() => {
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

it('defaults to TradingView and lets the user switch back to the existing professional ECharts chart', async () => {
  await act(async () => root.render(
    <StockDailyKChart symbol="510300.SH" dateRange={{ start: '2026-09-01', end: '2026-09-30' }} />,
  ))

  expect(host.querySelector('[data-testid="tradingview-chart"]')?.getAttribute('data-context'))
    .toBe('510300.SH|2026-09-01|2026-09-30')
  expect(host.querySelector('[data-testid="professional-chart"]')).toBeNull()

  const professionalButton = [...host.querySelectorAll('button')].find(button => button.textContent === '专业指标')
  expect(professionalButton).toBeDefined()
  act(() => professionalButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  expect(host.querySelector('[data-testid="professional-chart"]')).not.toBeNull()
  expect(host.querySelector('[data-testid="tradingview-chart"]')).toBeNull()
})

it('defaults to professional mode and explains when a backtest range must be kept visible', async () => {
  await act(async () => root.render(
    <StockDailyKChart
      symbol="510300.SH"
      dateRange={{ start: '2026-09-01', end: '2026-09-30' }}
      ranges={[{ start: '2026-09-28', end: '2026-09-30', label: '持仓区间' }]}
    />,
  ))

  expect(host.querySelector('[data-testid="professional-chart"]')).not.toBeNull()
  expect(host.querySelector('[data-testid="tradingview-chart"]')).toBeNull()
  expect(host.textContent).toContain('回测区间标注仅在专业指标图中完整显示')
  const tradingViewButton = [...host.querySelectorAll('button')].find(button => button.textContent === 'TradingView')
  expect(tradingViewButton?.disabled).toBe(true)
})
