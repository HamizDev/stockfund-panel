// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StockAnalysisBoard } from './StockAnalysis'
import { QK } from '@/lib/queryKeys'

const calls = vi.hoisted(() => ({ daily: vi.fn(), levels: vi.fn(), search: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { klineDaily: calls.daily, stockAnalysisLevels: calls.levels, instrumentSearch: calls.search } }))
vi.mock('@/components/stock-analysis/AnalysisKChart', () => ({ AnalysisKChart: ({ rows, onDateClick }: any) => <button onClick={() => onDateClick(rows[0].date)}>选择历史日线</button> }))
vi.mock('@/components/stock-analysis/AnalysisMinutePanel', () => ({ AnalysisMinutePanel: (props: any) => <div data-testid="minute" data-symbol={props.symbol} data-asset={props.assetType ?? ''} data-date={props.date ?? 'latest'} data-view={props.view}>
  <button onClick={() => props.onDateChange(null)}>最新分钟</button>
  <button onClick={() => props.onViewChange('candles')}>分钟蜡烛</button>
</div> }))

let host: HTMLDivElement, root: Root, query: QueryClient
const rows = [{ date: '2026-10-14', open: 1, high: 1.2, low: .9, close: 1.1, volume: 100 }]
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  sessionStorage.clear()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  query = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  calls.daily.mockReset().mockImplementation(async (symbol: string) => ({ symbol, rows }))
  calls.levels.mockReset().mockResolvedValue({ close: 1.1, levels: {} })
  calls.search.mockReset().mockImplementation(async (code: string) => ({ results: [
    { symbol: code === '510300' ? '510300.SH' : '600000.SH', name: '研究标的', asset_type: code === '510300' ? 'etf' : 'stock' },
  ] }))
})
afterEach(async () => { await act(async () => root.unmount()); query.clear(); host.remove() })
async function settle() { for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) }) }
async function render(symbol = '510300.SH') {
  await act(async () => root.render(<QueryClientProvider client={query}><StockAnalysisBoard key={symbol} symbol={symbol} /></QueryClientProvider>))
  await settle()
}
async function click(text: string) { await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === text)!.click()); await settle() }

it('loads ETF metadata and exposes latest minute data while the daily request is still pending', async () => {
  calls.daily.mockReturnValue(new Promise(() => {}))
  await render()
  const minute = host.querySelector<HTMLElement>('[data-testid="minute"]')!
  expect(minute.dataset.asset).toBe('etf')
  expect(minute.dataset.date).toBe('latest')
  expect(calls.daily).toHaveBeenCalledWith('510300.SH', 250)
})

it('links a day candle to minutes, remembers the view on remount and isolates another symbol', async () => {
  await render()
  await click('选择历史日线')
  await click('分钟蜡烛')
  expect(host.querySelector<HTMLElement>('[data-testid="minute"]')!.dataset.date).toBe('2026-10-14')
  await act(async () => root.render(<QueryClientProvider client={query}><div /></QueryClientProvider>))
  await render()
  expect(host.querySelector<HTMLElement>('[data-testid="minute"]')!.dataset.view).toBe('candles')
  expect(host.querySelector<HTMLElement>('[data-testid="minute"]')!.dataset.date).toBe('2026-10-14')
  await render('600000.SH')
  expect(host.querySelector<HTMLElement>('[data-testid="minute"]')!.dataset.date).toBe('latest')
  expect(host.querySelector<HTMLElement>('[data-testid="minute"]')!.dataset.asset).toBe('stock')
})

it('keeps minute data available if daily data fails and returns to latest on request', async () => {
  calls.daily.mockRejectedValue(new Error('daily unavailable'))
  await render()
  expect(host.textContent).toContain('日 K 数据加载失败')
  expect(host.querySelector('[data-testid="minute"]')).not.toBeNull()
  await click('最新分钟')
  expect(host.querySelector<HTMLElement>('[data-testid="minute"]')!.dataset.date).toBe('latest')
})

it('does not route an unidentified instrument to stock minute data', async () => {
  calls.search.mockResolvedValue({ results: [] })
  await render()
  expect(host.querySelector('[data-testid="minute"]')).toBeNull()
  expect(host.textContent).toContain('暂时无法识别标的类型')
})

it('preserves cached exact asset metadata when its background refresh fails', async () => {
  query.setQueryData(QK.instrumentSearch('510300', 'stock,etf,index'), {
    results: [{ symbol: '510300.SH', name: '沪深300ETF', asset_type: 'etf' }],
  }, { updatedAt: Date.now() - 600000 })
  calls.search.mockRejectedValue(new Error('metadata refresh unavailable'))
  await render()
  expect(host.querySelector<HTMLElement>('[data-testid="minute"]')?.dataset.asset).toBe('etf')
})
