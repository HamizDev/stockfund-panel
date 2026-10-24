// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StockAnalysisBoard } from './StockAnalysis'
import { QK } from '@/lib/queryKeys'

vi.mock('@/lib/useMediaQuery', () => ({ useMediaQuery: () => false }))

const calls = vi.hoisted(() => ({ daily: vi.fn(), levels: vi.fn(), search: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { stockAnalysisSnapshot: calls.daily, instrumentSearch: calls.search } }))
vi.mock('@/components/stock-analysis/AnalysisKChart', () => ({ AnalysisKChart: ({ rows, onDateClick, linkedPrice }: any) => <div data-testid="daily" data-linked-price={linkedPrice ?? ''} data-latest-date={rows.at(-1)?.date ?? ''}><button onClick={() => onDateClick(rows[0].date)}>选择历史日线</button></div> }))
vi.mock('@/components/stock-analysis/AnalysisMinutePanel', () => ({ AnalysisMinutePanel: (props: any) => <div data-testid="minute" data-symbol={props.symbol} data-asset={props.assetType ?? ''} data-date={props.date ?? 'latest'} data-view={props.view}>
  <button onClick={() => props.onDateChange(null)}>最新分钟</button>
  <button onClick={() => props.onViewChange('candles')}>分钟蜡烛</button>
  <button onClick={() => props.onPriceHover(1.05)}>指向分钟价</button>
  <button onClick={() => props.onPriceHover(null)}>离开分钟图</button>
</div> }))

let host: HTMLDivElement, root: Root, query: QueryClient
const rows = [{ date: '2026-10-14', open: 1, high: 1.2, low: .9, close: 1.1, volume: 100 }]
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  sessionStorage.clear()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  query = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  calls.daily.mockReset().mockImplementation(async (symbol: string) => ({ symbol, rows, analysis: { symbol, close: 1.1, levels: {} }, as_of: '2026-10-14', source: 'online', price_basis: 'unadjusted' }))
  calls.levels.mockReset().mockResolvedValue({ close: 1.1, levels: {} })
  calls.search.mockReset().mockImplementation(async (code: string) => ({ results: [
    { symbol: code === '510300' ? '510300.SH' : '600000.SH', name: '研究标的', asset_type: code === '510300' ? 'etf' : 'stock' },
  ] }))
})
afterEach(async () => { await act(async () => root.unmount()); query.clear(); host.remove() })
async function settle() { for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) }) }
async function render(symbol = '510300.SH', knownAssetType?: 'stock' | 'etf' | 'index') {
  await act(async () => root.render(<QueryClientProvider client={query}><StockAnalysisBoard key={symbol} symbol={symbol} knownAssetType={knownAssetType} /></QueryClientProvider>))
  await settle()
}
async function click(text: string) { await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === text)!.click()); await settle() }

it('loads ETF metadata and exposes latest minute data while the daily request is still pending', async () => {
  calls.daily.mockReturnValue(new Promise(() => {}))
  await render()
  const minute = host.querySelector<HTMLElement>('[data-testid="minute"]')!
  expect(minute.dataset.asset).toBe('etf')
  expect(minute.dataset.date).toBe('latest')
  expect(calls.daily).toHaveBeenCalledWith('510300.SH', 250, expect.any(AbortSignal))
})

it('uses a selected asset type immediately without issuing the metadata lookup', async () => {
  await render('510300.SH', 'etf')
  expect(host.querySelector<HTMLElement>('[data-testid="minute"]')?.dataset.asset).toBe('etf')
  expect(calls.search).not.toHaveBeenCalled()
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
  await render('510300.SH')
  expect(host.querySelector<HTMLElement>('[data-testid="minute"]')!.dataset.date).toBe('latest')
  expect(host.querySelector<HTMLElement>('[data-testid="minute"]')!.dataset.view).toBe('intraday')
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

it('retains the current snapshot on route unmount, releases the old instrument on switch and cancels its request', async () => {
  await render()
  const key = QK.stockAnalysisSnapshot('510300.SH', 250)
  const before = query.getQueryData(key)
  await act(async () => root.render(<QueryClientProvider client={query}><div /></QueryClientProvider>))
  expect(query.getQueryData(key)).toBe(before)
  await render()
  expect(calls.daily).toHaveBeenCalledTimes(1)
  await render('600000.SH')
  expect(query.getQueryData(key)).toBeUndefined()
  expect(query.getQueryData(QK.stockAnalysisSnapshot('600000.SH', 250))).toBeTruthy()
})

it('shows actual source date and keeps a successful chart during a failed refresh', async () => {
  await render()
  expect(host.textContent).toContain('日线截至 2026-10-14')
  expect(host.textContent).toContain('未复权')
  calls.daily.mockRejectedValueOnce(new Error('offline'))
  await click('更新日线')
  expect(host.textContent).toContain('日线更新失败，保留上次有效图表。')
  expect(host.textContent).toContain('选择历史日线')
})

it('shows the provisional daily bar using its actual intraday quote time and retains it on refresh failure', async () => {
  const provisionalRows = [
    { ...rows[0], date: '2026-10-21', close: 1.1 },
    { ...rows[0], date: '2026-10-22', open: 1.1, high: 1.13, low: 1.08, close: 1.12 },
  ]
  calls.daily.mockImplementation(async (symbol: string) => ({
    symbol, rows: provisionalRows,
    analysis: { symbol, close: 1.12, levels: {} },
    as_of: '2026-10-22', daily_as_of: '2026-10-21',
    intraday_as_of: '2026-10-22T03:30:00Z', is_provisional: true,
    checked_at: '2026-10-22T03:31:00Z', source: 'online', price_basis: 'unadjusted',
  }))
  await render()

  expect(host.querySelector<HTMLElement>('[data-testid="daily"]')?.dataset.latestDate).toBe('2026-10-22')
  expect(host.textContent).toContain('含当日未收盘日K（2026-10-22）')
  expect(host.textContent).toContain('正式日线截至 2026-10-21')
  expect(host.textContent).toContain('行情截至 2026-10-22 11:30（北京时间）')
  expect(host.textContent).not.toContain('11:31')
  const snapshotQuery = query.getQueryCache().find({ queryKey: QK.stockAnalysisSnapshot('510300.SH', 250) })
  const snapshotObserver = snapshotQuery?.observers[0]
  expect(snapshotObserver).toBeDefined()
  expect(typeof snapshotObserver?.options.refetchInterval).toBe('function')

  calls.daily.mockRejectedValueOnce(new Error('snapshot refresh unavailable'))
  await click('更新日线')
  expect(host.querySelector<HTMLElement>('[data-testid="daily"]')?.dataset.latestDate).toBe('2026-10-22')
  expect(host.textContent).toContain('含当日未收盘日K（2026-10-22）')
  expect(host.textContent).toContain('正式日线截至 2026-10-21')
  expect(host.textContent).toContain('行情截至 2026-10-22 11:30（北京时间）')
  expect(host.textContent).toContain('日线更新失败，保留上次有效图表。')
})

it('never displays a returned snapshot from another instrument', async () => {
  calls.daily.mockResolvedValue({ symbol: '600000.SH', rows, analysis: { symbol: '600000.SH' } })
  await render()
  expect(host.textContent).toContain('日 K 数据加载失败')
  expect(host.textContent).not.toContain('选择历史日线')
})

it('does not replace a newer online chart with an older local fallback', async () => {
  await render()
  calls.daily.mockResolvedValueOnce({ symbol: '510300.SH', rows: [{ ...rows[0], date: '2026-10-09' }],
    analysis: { symbol: '510300.SH', levels: {} }, as_of: '2026-10-09', source: 'local',
    warning: '在线日线查询失败，显示本地已有数据' })
  await click('更新日线')
  expect(host.textContent).toContain('日线截至 2026-10-14')
  expect(host.textContent).not.toContain('日线截至 2026-10-09')
  expect(host.textContent).toContain('在线日线查询失败')
})

function cacheMinute(date = '2026-10-14', close = 1.1) {
  query.setQueryData(QK.analysisMinute('510300.SH', '', 'stock'), {
    symbol: '510300.SH', date, rows: [{ datetime: `${date} 15:00:00`, close }],
  })
}

it('links a minute price to the matching daily chart without reloading data and clears it on leave', async () => {
  cacheMinute()
  await render()
  await click('指向分钟价')
  expect(host.querySelector<HTMLElement>('[data-testid="daily"]')!.dataset.linkedPrice).toBe('1.05')
  expect(calls.daily).toHaveBeenCalledTimes(1)
  await click('离开分钟图')
  expect(host.querySelector<HTMLElement>('[data-testid="daily"]')!.dataset.linkedPrice).toBe('')
})

it('clears linked prices on date, chart type and instrument changes', async () => {
  cacheMinute()
  await render()
  await click('指向分钟价')
  await click('选择历史日线')
  expect(host.querySelector<HTMLElement>('[data-testid="daily"]')!.dataset.linkedPrice).toBe('')
  await click('最新分钟')
  cacheMinute()
  await click('指向分钟价')
  await click('分钟蜡烛')
  expect(host.querySelector<HTMLElement>('[data-testid="daily"]')!.dataset.linkedPrice).toBe('')
  await click('指向分钟价')
  await render('600000.SH')
  expect(host.querySelector<HTMLElement>('[data-testid="daily"]')!.dataset.linkedPrice).toBe('')
})

it('does not mix minute prices with a daily chart in a different adjusted basis', async () => {
  cacheMinute('2026-10-14', 2.2)
  await render()
  await click('指向分钟价')
  expect(host.querySelector<HTMLElement>('[data-testid="daily"]')!.dataset.linkedPrice).toBe('')
  expect(host.textContent).toContain('未能核验日 K 与分时价格是否一致')
})

it('uses a matching previous-close anchor while the current session is not closed', async () => {
  query.setQueryData(QK.analysisMinute('510300.SH', '', 'stock'), {
    symbol: '510300.SH', date: '2026-10-15', prev_close: 1.1, prev_close_date: '2026-10-14',
    rows: [{ datetime: '2026-10-15 10:00:00', close: 1.05 }],
  })
  await render()
  await click('指向分钟价')
  expect(host.querySelector<HTMLElement>('[data-testid="daily"]')!.dataset.linkedPrice).toBe('1.05')
})
