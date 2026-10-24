// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StockDailyKChart } from './StockDailyKChart'

const fixture = vi.hoisted(() => ({ rows: [
  { date: '2026-10-12', open: 11, high: 12, low: 10, close: 11, volume: 100 },
  { date: '2026-10-13', open: 13, high: 14, low: 12, close: 13, volume: 300 },
  { date: '2026-10-14', open: 14, high: 15, low: 13, close: 14, volume: 200 },
] }))
vi.mock('@tanstack/react-query', () => ({ useQuery: () => ({ data: fixture, isLoading: false, isError: false }) }))
vi.mock('@/lib/kline', () => ({ klineDailyQueryOptions: () => ({ queryKey: ['test'] }) }))
vi.mock('@/lib/storage', () => ({
  storage: {
    stockVolumeCompare: { get: () => ({ enabled: true, days: 1 }), set: vi.fn() },
    stockChartIndicators: { get: (fallback: unknown) => fallback, set: vi.fn() },
    stockChartMode: { get: (fallback: unknown) => fallback, set: vi.fn() },
  },
}))
vi.mock('@/components/research/ResearchAIButton', () => ({ ResearchAIButton: () => null }))
vi.mock('@/components/EChartsCandlestick', () => ({
  SUB_CHARTS: [
    { key: 'adx', label: 'ADX 趋势', height: 84 },
    { key: 'vol', label: '成交量', height: 84 },
    { key: 'macd', label: 'MACD', height: 84 },
    { key: 'rsi', label: 'RSI', height: 84 },
    { key: 'kdj', label: 'KDJ', height: 84 },
  ],
  OVERLAY_INDICATORS: [{ key: 'boll', label: 'BOLL' }],
  EChartsCandlestick: () => null,
}))
vi.mock('@/components/TradingViewDailyChart', () => ({
  TradingViewDailyChart: ({ anchoredVwap }: { anchoredVwap: Array<{ value: number | null }> }) =>
    <output data-testid="anchor-values">{JSON.stringify(anchoredVwap.map(point => point.value))}</output>,
}))

let root: Root
let host: HTMLDivElement
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })

const range = { start: '2026-10-12', end: '2026-10-14' }
async function render(symbol: string, dateRange = range) {
  await act(async () => root.render(<StockDailyKChart symbol={symbol} dateRange={dateRange} />))
}
async function anchor() {
  await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === '研究指标')!.click())
  const select = host.querySelector<HTMLSelectElement>('[aria-label="锚定VWAP起点"]')!
  await act(async () => { select.value = '2026-10-13'; select.dispatchEvent(new Event('change', { bubbles: true })) })
}

it('clears the real anchor state across A to B to A, not only its presentation', async () => {
  await render('518880.SH')
  await anchor()
  expect(host.querySelector('output')?.textContent).toBe('[null,13,13.4]')
  await render('511360.SH')
  await render('518880.SH')
  expect(host.querySelector<HTMLSelectElement>('[aria-label="锚定VWAP起点"]')?.value).toBe('')
  expect(host.querySelector('output')?.textContent).toBe('[null,null,null]')
})
it('preserves a same-context refresh but clears a changed range even when returning', async () => {
  await render('518880.SH')
  await anchor()
  await render('518880.SH')
  expect(host.querySelector('output')?.textContent).toBe('[null,13,13.4]')
  await render('518880.SH', { start: '2026-10-01', end: '2026-10-14' })
  await render('518880.SH')
  expect(host.querySelector('output')?.textContent).toBe('[null,null,null]')
})
