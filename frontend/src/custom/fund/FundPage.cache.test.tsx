// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { QK } from '@/lib/queryKeys'
import { FundPage } from './FundPage'

const fixtures = vi.hoisted(() => ({
  watchlist: vi.fn(),
  portfolio: vi.fn(),
  profile: vi.fn(),
  quote: vi.fn(),
  kline: vi.fn(),
  nav: vi.fn(),
  estimate: vi.fn(),
  savePortfolio: vi.fn(),
}))

vi.mock('./client', () => ({
  fundApi: {
    search: vi.fn(), watchlist: fixtures.watchlist, addWatch: vi.fn(), removeWatch: vi.fn(),
    portfolio: fixtures.portfolio, savePortfolio: fixtures.savePortfolio, removePosition: vi.fn(),
    quote: fixtures.quote, kline: fixtures.kline, nav: fixtures.nav, profile: fixtures.profile,
    estimate: fixtures.estimate, research: vi.fn(), analyzeStream: vi.fn(), aiPickStream: vi.fn(), screener: vi.fn(),
  },
  pctText: (value: number | null | undefined) => value == null ? '--' : `${(value * 100).toFixed(2)}%`,
}))

vi.mock('@/components/EChartsCandlestick', () => ({
  EChartsCandlestick: ({ data }: { data: { date: string }[] }) => <div data-testid="daily-bars">{data.map(row => row.date).join(',')}</div>,
}))
vi.mock('./FundScreener', () => ({ FundScreener: () => null }))
vi.mock('./FundResearchPanel', () => ({ FundResearchPanel: () => null }))
vi.mock('@/components/HoldingsDailyReview', () => ({
  HoldingsDailyReview: ({ loaded, holdingSignature }: { loaded: boolean; holdingSignature: string }) => (
    <div data-testid="holdings-review" data-loaded={String(loaded)} data-signature={holdingSignature} />
  ),
  holdingsReviewSignature: (items: { thscode: string; amount: number; profit: number }[]) =>
    items.map(item => `${item.thscode}:${item.amount}:${item.profit}`).join('|'),
}))

const symbol = '510300.SH'
const oldProfile = {
  thscode: symbol, ticker: '510300', fund_name: '缓存的沪深300ETF全称', estab_date: null,
  mgmt_name: null, manager_name: null, fund_type: 'ETF', benchmark: null,
}
const oldQuote = {
  thscode: symbol, symbol: '510300', name: '沪深300ETF', last_price: 4.123, prev_close: 4.1,
  open: 4.11, high: 4.13, low: 4.1, change_pct: 0.0056, change_amount: 0.023,
  volume: 1000, amount: 1_000_000, amplitude: 0.01, turnover_rate: 0.02, timestamp: 1_792_000_000,
}
const oldKline = {
  thscode: symbol, adjusted: '前复权',
  bars: [{ date: '2026-10-19', open: 4.1, high: 4.2, low: 4.0, close: 4.123, volume: 100, amount: 410 }],
}

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  fixtures.watchlist.mockResolvedValue({ items: [] })
  fixtures.portfolio.mockResolvedValue({ items: [] })
  fixtures.savePortfolio.mockResolvedValue({})
  fixtures.profile.mockResolvedValue({ profile: oldProfile })
  fixtures.quote.mockResolvedValue({ quote: oldQuote })
  fixtures.kline.mockResolvedValue(oldKline)
  fixtures.nav.mockResolvedValue({ thscode: symbol, nav: [] })
  fixtures.estimate.mockRejectedValue(new Error('not needed'))
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
  vi.clearAllMocks()
})

async function render(path: string) {
  await act(async () => root.render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={client}><FundPage /></QueryClientProvider>
    </MemoryRouter>,
  ))
  for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

function seedDetailQuery<T>(key: readonly unknown[], data: T) {
  client.setQueryDefaults(key, { meta: { instrumentView: 'fund-detail', symbol } })
  client.setQueryData(key, data, { updatedAt: Date.now() })
}

it('keeps cached ETF detail visible after leaving and returning while stale data refreshes', async () => {
  const profileKey = QK.fundProfile(symbol)
  const quoteKey = QK.fundQuote(symbol)
  const klineKey = QK.fundKline(symbol, 250)
  seedDetailQuery(profileKey, { profile: oldProfile })
  seedDetailQuery(quoteKey, { quote: oldQuote })
  seedDetailQuery(klineKey, oldKline)

  await render(`/fund?code=${symbol}&name=沪深300ETF`)
  expect(host.textContent).toContain('缓存的沪深300ETF全称')
  expect(host.textContent).toContain('4.123')
  expect(host.querySelector('[data-testid="daily-bars"]')?.textContent).toContain('2026-10-19')
  expect(fixtures.quote).not.toHaveBeenCalled()

  await act(async () => root.render(<div>离开基金详情</div>))
  await act(async () => {
    client.setQueryData(quoteKey, { quote: oldQuote }, { updatedAt: Date.now() - 11_000 })
    client.setQueryData(klineKey, oldKline, { updatedAt: Date.now() - 301_000 })
    client.setQueryData(profileKey, { profile: oldProfile }, { updatedAt: Date.now() - 24 * 60 * 60_000 - 1 })
  })
  fixtures.quote.mockRejectedValueOnce(new Error('行情刷新失败'))
  fixtures.kline.mockRejectedValueOnce(new Error('日K刷新失败'))
  fixtures.profile.mockRejectedValueOnce(new Error('资料刷新失败'))

  await render(`/fund?code=${symbol}&name=沪深300ETF`)
  expect(host.textContent).toContain('缓存的沪深300ETF全称')
  expect(host.textContent).toContain('4.123')
  expect(host.querySelector('[data-testid="daily-bars"]')?.textContent).toContain('2026-10-19')
  expect(host.textContent).not.toContain('加载中')
  expect(fixtures.quote).toHaveBeenCalledTimes(1)
  expect(fixtures.kline).toHaveBeenCalledTimes(1)
  expect(fixtures.profile).toHaveBeenCalledTimes(1)
  expect(client.getQueryData(quoteKey)).toEqual({ quote: oldQuote })
})

it('keeps cached portfolio rows and marks review loaded when background refresh fails', async () => {
  const portfolio = { items: [{ thscode: '005827.OF', name: '易方达蓝筹精选', amount: 1200, profit: -18 }] }
  client.setQueryData(QK.fundPortfolio, portfolio, { updatedAt: Date.now() - 16_000 })
  fixtures.portfolio.mockRejectedValueOnce(new Error('持仓后台刷新失败'))

  await render('/fund')

  expect(host.textContent).toContain('易方达蓝筹精选')
  expect(host.textContent).toContain('¥1200.00')
  const review = host.querySelector('[data-testid="holdings-review"]')
  expect(review?.getAttribute('data-loaded')).toBe('true')
  expect(review?.getAttribute('data-signature')).toBe('005827.OF:1200:-18')
  expect(fixtures.portfolio).toHaveBeenCalledTimes(1)
})

it('cancels a pre-save portfolio GET before refetching after adding a holding', async () => {
  let initialSignal: AbortSignal | undefined
  let resolveInitial!: (value: { items: [] }) => void
  let calls = 0
  const saved = { items: [{ thscode: symbol, name: '沪深300ETF', amount: 1000, profit: 0 }] }
  fixtures.portfolio.mockImplementation((signal?: AbortSignal) => {
    calls += 1
    if (calls === 1) {
      initialSignal = signal
      return new Promise(resolve => { resolveInitial = resolve })
    }
    if (calls === 2) return Promise.resolve({ items: [] })
    return Promise.resolve(saved)
  })

  await render(`/fund?code=${symbol}&name=沪深300ETF`)
  expect(initialSignal).toBeDefined()
  const addButton = Array.from(host.querySelectorAll('button')).find(button => button.textContent?.includes('加入持仓'))
  expect(addButton).toBeDefined()
  await act(async () => addButton!.click())

  const amount = host.querySelector<HTMLInputElement>('input[type="number"]')
  expect(amount).not.toBeNull()
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    setter?.call(amount, '1000')
    amount!.dispatchEvent(new Event('input', { bubbles: true }))
  })
  const saveButton = Array.from(host.querySelectorAll('button')).find(button => button.textContent?.trim() === '保存')
  expect(saveButton).toBeDefined()
  await act(async () => saveButton!.click())
  await render(`/fund?code=${symbol}&name=沪深300ETF`)

  expect(fixtures.savePortfolio).toHaveBeenCalledTimes(1)
  expect(calls).toBe(3)
  expect(initialSignal?.aborted).toBe(true)
  expect(client.getQueryData(QK.fundPortfolio)).toEqual(saved)

  await act(async () => root.render(<div>离开基金详情</div>))
  await render('/fund')
  expect(host.textContent).toContain('沪深300ETF')
  expect(host.textContent).toContain('¥1000.00')
  resolveInitial({ items: [] })
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  expect(client.getQueryData(QK.fundPortfolio)).toEqual(saved)
})
