// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { CanvasMarketChartProps } from '@/components/CanvasMarketChart'
import { QK } from '@/lib/queryKeys'
import { DASHBOARD_QUERY_GC_MS } from '@/lib/dashboardQueryCache'
import type { GlobalMarket, GlobalMarketChartInterval } from './types'
import GlobalDashboard from './GlobalDashboard'

const mock = vi.hoisted(() => ({ overview: vi.fn(), chart: vi.fn(), canvas: null as CanvasMarketChartProps | null }))
vi.mock('./client', () => ({ globalMarketClient: { overview: mock.overview, chart: mock.chart } }))
vi.mock('@/components/CanvasMarketChart', () => ({ CanvasMarketChart: (props: CanvasMarketChartProps) => {
  mock.canvas = props
  return <div data-testid="canvas" />
} }))
vi.mock('./GlobalIndustryPanel', () => ({ GlobalIndustryPanel: () => <div>全球产业测试入口</div> }))

const rows = (market: GlobalMarket) => [
  { symbol: market === 'hk' ? '^HSI' : '^GSPC', name: market === 'hk' ? '恒生指数' : '标普500', kind: 'index' },
  { symbol: market === 'hk' ? '00700.HK' : 'AAPL', name: market === 'hk' ? '腾讯控股' : '苹果', kind: 'stock' },
  { symbol: market === 'hk' ? '02800.HK' : 'SPY', name: market === 'hk' ? '盈富基金' : '标普ETF', kind: 'etf' },
].map(item => ({ ...item, market, source: 'yahoo_public', price: 123.45, change_pct: .0123, quote_at_ms: Date.parse('2026-10-16T08:00:00Z'), fetched_at_ms: 200, stale: false, error: null, status: 'ok' }))

function completeOverview(market: GlobalMarket) {
  const isHk = market === 'hk'
  const snapshotRow = {
    market,
    symbol: isHk ? '00005.HK' : 'MSFT',
    name: '测试广度股',
    price: 100,
    change_pct: .02,
    amount: 1000,
    volume: 100,
    turnover_rate: .01,
    industry: null,
    quote_at_ms: 1792137600000,
    fetched_at_ms: 1792137610000,
    source: isHk ? 'sina_hk_public' : 'yahoo_screener',
    kind: 'stock',
  }
  return {
    market,
    currency: isHk ? 'HKD' : 'USD',
    timezone: isHk ? 'Asia/Hong_Kong' : 'America/New_York',
    scope: 'fixed_watchlist',
    source: 'yahoo_public',
    loading: false,
    refresh_after_seconds: 60,
    checked_at_ms: 1792137610000,
    completed_count: 3,
    items: rows(market),
    market_snapshot: {
      loading: false,
      stale: false,
      error: null,
      data: {
        market,
        scope: isHk ? 'hk_sina_hkd_pool' : 'us_screener_pool',
        universe_label: '测试覆盖池',
        total_reported: 1,
        received: 1,
        complete: true,
        skipped: 0,
        date: '2026-10-16',
        source: isHk ? 'sina_hk_public' : 'yahoo_screener',
        currency: isHk ? 'HKD' : 'USD',
        timezone: isHk ? 'Asia/Hong_Kong' : 'America/New_York',
        quote_at_ms: 1792137600000,
        fetched_at_ms: 1792137610000,
        stats: { covered: 1, up: 1, flat: 0, down: 0, strong: 0, weak: 0, mean: .02, median: .02, bins: [0, 0, 0, 0, 0, 1, 0, 0] },
        ranks: { gainers: [snapshotRow], losers: [], amount: [], turnover: [], volume: [], market_cap: [] },
        industries: [],
        industry_covered: 0,
      },
    },
  }
}

let host: HTMLDivElement
let root: Root
let qc: QueryClient
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  sessionStorage.clear()
  mock.overview.mockReset().mockImplementation(async (market: GlobalMarket) => ({ market, items: rows(market), loading: false }))
  mock.chart.mockReset().mockImplementation(async (market: GlobalMarket, symbol: string, interval: GlobalMarketChartInterval) => ({
    market, symbol, interval, name: 'chart name', kind: symbol.startsWith('^') ? 'index' : 'stock',
    quote: { price: 123.45, change_pct: .0123 }, quote_at_ms: 1792137600000, fetched_at_ms: 1792137610000,
    stale: false, error: null, warnings: [], bars: [{ time: '2026-10-16', open: 1, high: 2, low: 1, close: 2, volume: 10 }],
  }))
  host = document.createElement('div'); document.body.append(host)
  root = createRoot(host)
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})
afterEach(async () => { await act(async () => root.unmount()); qc.clear(); host.remove(); vi.useRealTimers() })

async function render(market: GlobalMarket) {
  await act(async () => root.render(<MemoryRouter><QueryClientProvider client={qc}><GlobalDashboard key={market} market={market} /></QueryClientProvider></MemoryRouter>))
  await settle()
}
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)) }) }
async function click(text: string) {
  const button = [...host.querySelectorAll('button')].find(button => button.textContent?.includes(text))!
  expect(button).toBeTruthy()
  await act(async () => button.click())
  await settle()
}

it('uses shared board navigation, index points, and real currency only for securities', async () => {
  await render('hk')
  expect(host.querySelector('h1')?.textContent).toBe('港股看板')
  expect(host.querySelector('nav[aria-label="看板市场切换"]')?.textContent).toContain('美股看板')
  expect(host.textContent).toContain('覆盖池与观察池均不代表全市场')
  expect(host.textContent).not.toContain('Yahoo核心观察报价')
  expect(host.textContent).not.toContain('报价来源')
  expect(host.textContent).not.toContain('东财公开行情')
  expect(mock.canvas?.formatPrice?.(100)).toBe('100.00 点')
  expect(host.textContent).toContain('+1.23%')
  await click('腾讯控股')
  expect(mock.canvas?.symbol).toBe('00700.HK')
  expect(mock.canvas?.formatPrice?.(100)).toBe('HK$100.00')
})

it('restores independent symbol/interval selection and does not flash another market', async () => {
  await render('hk')
  const defaultChartKey = QK.globalMarketChart('hk', '^HSI', '1d')
  await click('腾讯控股')
  await click('1分钟')
  expect(qc.getQueryState(defaultChartKey)).toBeUndefined()
  expect(qc.getQueryCache().find({ queryKey: QK.globalMarketChart('hk', '00700.HK', '1m') })?.options.meta)
    .toMatchObject({ instrumentView: 'global-dashboard-hk', symbol: '00700.HK' })
  await render('us')
  expect(host.querySelector('h1')?.textContent).toBe('美股看板')
  expect(host.textContent).not.toContain('腾讯控股')
  expect(mock.canvas?.symbol).toBe('^GSPC')
  expect(mock.overview).toHaveBeenCalledWith('us', expect.any(AbortSignal))
  expect(qc.getQueryData(QK.globalMarketOverview('hk'))).toMatchObject({ market: 'hk' })
  expect(qc.getQueryData(QK.globalMarketOverview('us'))).toMatchObject({ market: 'us' })
  await render('hk')
  expect(mock.canvas?.symbol).toBe('00700.HK')
  expect(mock.chart).toHaveBeenCalledWith('hk', '00700.HK', '1m', expect.any(AbortSignal))
})

it('renders immediate progress, partial missing quotes, explicit stale and refresh errors', async () => {
  const items = rows('us')
  items[1] = { ...items[1], price: null as unknown as number, change_pct: null as unknown as number, error: '限流' as unknown as null, status: 'error' }
  items[2] = { ...items[2], stale: true, error: '离线' as unknown as null, status: 'error' }
  mock.overview.mockResolvedValue({ market: 'us', items, loading: true })
  mock.chart.mockRejectedValue(new Error('图表离线'))
  await render('us')
  expect(host.textContent).toContain('取数中')
  expect(host.textContent).toContain('限流')
  expect(host.textContent).toContain('旧缓存 · 离线')
  expect(host.textContent).toContain('图表加载失败')
  expect(host.textContent).not.toContain('+0%')
  expect(host.textContent).not.toContain('情绪评分')
  mock.overview.mockRejectedValue(new Error('看板离线'))
  await click('刷新')
  expect(host.textContent).toContain('看板更新失败')
  expect(host.textContent).toContain('保留最近可用数据')
})

it('ignores late chart responses from the previous symbol', async () => {
  let finish: (value: unknown) => void = () => {}
  const normal = mock.chart.getMockImplementation()!
  mock.chart.mockImplementation((market, symbol, interval) => symbol === '^HSI' ? new Promise(resolve => { finish = resolve }) : normal(market, symbol, interval))
  await render('hk')
  await click('腾讯控股')
  expect(mock.canvas?.symbol).toBe('00700.HK')
  await act(async () => finish(await normal('hk', '^HSI', '1d')))
  await settle()
  expect(mock.canvas?.symbol).toBe('00700.HK')
})

it('keeps the previous complete overview when the same market returns an empty loading response', async () => {
  const complete = completeOverview('hk')
  mock.overview.mockResolvedValueOnce(complete)
  await render('hk')
  expect(host.textContent).toContain('腾讯控股')
  expect(host.textContent).toContain('测试广度股')

  mock.overview.mockResolvedValueOnce({
    ...complete,
    loading: true,
    completed_count: 0,
    items: [],
    market_snapshot: { loading: true, stale: false, error: null, data: null },
  })
  await click('刷新')

  expect(host.textContent).toContain('腾讯控股')
  expect(host.textContent).toContain('测试广度股')
  expect(qc.getQueryData(QK.globalMarketOverview('hk'))).toMatchObject({
    market: 'hk',
    items: expect.arrayContaining([expect.objectContaining({ symbol: '00700.HK' })]),
    market_snapshot: { data: expect.objectContaining({ market: 'hk' }) },
  })
})

it('reuses cached overview and chart after more than five idle minutes while refreshes are pending', async () => {
  vi.useFakeTimers()
  await act(async () => {
    root.render(<MemoryRouter><QueryClientProvider client={qc}><GlobalDashboard key="hk" market="hk" /></QueryClientProvider></MemoryRouter>)
    await vi.advanceTimersByTimeAsync(25)
  })
  await act(async () => { await Promise.resolve() })
  const overviewKey = QK.globalMarketOverview('hk')
  const chartKey = QK.globalMarketChart('hk', '^HSI', '1d')
  const cachedOverview = qc.getQueryData(overviewKey)
  const cachedChart = qc.getQueryData(chartKey)
  expect(cachedOverview).toBeDefined()
  expect(cachedChart).toBeDefined()

  await act(async () => root.render(<div />))
  await vi.advanceTimersByTimeAsync(5 * 60_000 + 1)
  expect(qc.getQueryData(overviewKey)).toEqual(cachedOverview)
  expect(qc.getQueryData(chartKey)).toEqual(cachedChart)

  mock.overview.mockImplementationOnce(() => new Promise(() => {}))
  mock.chart.mockImplementationOnce((_market, _symbol, _interval, signal) => new Promise((_resolve, reject) => {
    signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
  }))
  await act(async () => {
    root.render(<MemoryRouter><QueryClientProvider client={qc}><GlobalDashboard key="hk" market="hk" /></QueryClientProvider></MemoryRouter>)
    await Promise.resolve()
  })

  expect(qc.getQueryCache().find({ queryKey: overviewKey })?.options.gcTime).toBe(DASHBOARD_QUERY_GC_MS)
  expect(qc.getQueryCache().find({ queryKey: chartKey })?.options.gcTime).toBe(DASHBOARD_QUERY_GC_MS)
  expect(qc.getQueryState(overviewKey)?.fetchStatus).toBe('fetching')
  expect(qc.getQueryState(chartKey)?.fetchStatus).toBe('fetching')
  expect(host.textContent).toContain('后台更新中 · 暂保留上次完整快照')
  expect(host.textContent).toContain('腾讯控股')
  expect(host.textContent).not.toContain('正在加载指数与观察池')
  expect(host.textContent).not.toContain('正在加载恒生指数K线')
  expect(mock.canvas?.symbol).toBe('^HSI')
})

it('keeps a cached chart visible and reports an error when a background refresh fails', async () => {
  await render('us')
  expect(mock.canvas?.symbol).toBe('^GSPC')

  mock.chart.mockRejectedValueOnce(new Error('图表刷新失败'))
  await click('刷新')

  expect(host.textContent).toContain('图表加载失败')
  expect(host.textContent).toContain('图表刷新失败')
  expect(host.querySelector('[data-testid="canvas"]')).not.toBeNull()
  expect(mock.canvas?.symbol).toBe('^GSPC')
})
