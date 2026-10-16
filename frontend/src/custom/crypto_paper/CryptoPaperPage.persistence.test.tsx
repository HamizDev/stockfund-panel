// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CryptoPaperPage } from './CryptoPaperPage'
import { cryptoApi, type CryptoStrategyAccount } from './client'

const fixtures = vi.hoisted(() => ({ accounts: [] as unknown[], queryKeys: [] as unknown[][] }))

vi.mock('@tanstack/react-query', async () => {
  const React = await vi.importActual<typeof import('react')>('react')
  return { useQuery: (options: { queryKey: unknown[]; queryFn: () => Promise<unknown>; enabled?: boolean }) => {
    const [data, setData] = React.useState<unknown>(undefined)
    React.useEffect(() => {
      fixtures.queryKeys.push(options.queryKey)
      if (options.enabled === false) return
      let alive = true
      void options.queryFn().then(result => { if (alive) setData(result) }).catch(() => undefined)
      return () => { alive = false }
    }, [JSON.stringify(options.queryKey), options.enabled])
    return { data, error: null, isPending: false, refetch: vi.fn() }
  } }
})

vi.mock('@/components/PageHeader', () => ({ PageHeader: ({ title }: { title: string }) => <h1>{title}</h1> }))
vi.mock('./CryptoMarketChart', () => ({
  CryptoMarketChart: ({ symbol, interval }: { symbol: string; interval: string }) => <div data-testid="chart" data-symbol={symbol} data-interval={interval} />,
}))
vi.mock('./StrategyAccountsPanel', () => ({
  StrategyAccountsPanel: ({ onAccountsLoaded }: { onAccountsLoaded?: (accounts: CryptoStrategyAccount[]) => void }) => <button onClick={() => onAccountsLoaded?.(fixtures.accounts as CryptoStrategyAccount[])}>加载账户列表</button>,
}))
vi.mock('./ResearchWorkflowPanel', () => ({ ResearchWorkflowPanel: () => <div data-testid="research-workflow" /> }))
vi.mock('./client', () => ({ cryptoApi: {
  marketQuote: vi.fn(), candles: vi.fn(), marketStream: vi.fn(), strategyAccountDetail: vi.fn(),
  account: vi.fn(), valuation: vi.fn(), quote: vi.fn(), order: vi.fn(),
} }))

function account(id: string): CryptoStrategyAccount {
  return {
    id, name: 'Bitget 研究账户', exchange: 'bitget', market: 'usdm', symbol: 'BTCUSDT', strategy_id: 'ema_trend',
    interval: '1h', leverage: 2, initial_cash: '1000', allocation_pct: 10, stop_loss_pct: 2, take_profit_pct: 4,
    enabled: false, status: 'paused', last_error: null, last_check_ms: null, last_signal: null, last_bar_time_ms: null,
    cash: '1000', equity: '1000', total_pnl: '0', return_pct: 0, max_drawdown_pct: 0, fee_total: '0',
    funding_total: '0', liquidation_count: 0, trade_count: 0, positions: {},
  }
}

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  sessionStorage.clear()
  fixtures.accounts = [account('account-1')]
  fixtures.queryKeys = []
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  vi.mocked(cryptoApi.marketQuote).mockResolvedValue({ quote: null, stream: { connected: false, fresh: false, asof_ms: null, error: null, source: '' } } as never)
  vi.mocked(cryptoApi.candles).mockResolvedValue({ bars: [] } as never)
  vi.mocked(cryptoApi.marketStream).mockReturnValue({ close: vi.fn(), onmessage: null, onerror: null } as never)
  vi.mocked(cryptoApi.strategyAccountDetail).mockImplementation(async (id: string) => ({ account: account(id), trades: [], nav: [] }))
  vi.mocked(cryptoApi.account).mockReset()
  vi.mocked(cryptoApi.strategyAccountDetail).mockClear()
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

async function settle() {
  for (let index = 0; index < 5; index++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

async function render() {
  await act(async () => root.render(<MemoryRouter><CryptoPaperPage /></MemoryRouter>))
  await settle()
}

it('restores chart choices and reattaches only a server-confirmed Bitget account without replacing the chart period', async () => {
  sessionStorage.setItem('page-view:crypto-market', JSON.stringify({ symbol: 'SOLUSDT', interval: '5m', selectedId: 'account-1' }))
  await render()
  expect(host.querySelector<HTMLSelectElement>('[aria-label="图表交易对"]')?.value).toBe('SOLUSDT')
  expect(host.querySelector<HTMLSelectElement>('[aria-label="图表周期"]')?.value).toBe('5m')

  const interval = host.querySelector<HTMLSelectElement>('[aria-label="图表周期"]')!
  await act(async () => { interval.value = '15m'; interval.dispatchEvent(new Event('change', { bubbles: true })) })
  await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === '加载账户列表')!.click())
  await settle()

  expect(host.querySelector('[data-testid="chart"]')?.getAttribute('data-symbol')).toBe('SOLUSDT')
  expect(host.querySelector('[data-testid="chart"]')?.getAttribute('data-interval')).toBe('15m')
  expect(host.textContent).toContain('图表关联：Bitget 研究账户')
  expect(cryptoApi.strategyAccountDetail).toHaveBeenCalledWith('account-1')
  expect(cryptoApi.account).not.toHaveBeenCalled()
  const saved = JSON.parse(sessionStorage.getItem('page-view:crypto-market') ?? 'null')
  expect(saved).toEqual({ symbol: 'SOLUSDT', interval: '15m', selectedId: 'account-1' })
})

it('clears a stale saved account association and keeps the Binance manual ledger absent', async () => {
  sessionStorage.setItem('page-view:crypto-market', JSON.stringify({ symbol: 'BTCUSDT', interval: '1h', selectedId: 'deleted-account' }))
  await render()
  await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === '加载账户列表')!.click())
  await settle()

  const saved = JSON.parse(sessionStorage.getItem('page-view:crypto-market') ?? 'null')
  expect(saved.selectedId).toBeNull()
  expect(host.textContent).not.toContain('Binance 手动账本')
  expect(cryptoApi.account).not.toHaveBeenCalled()
  expect(cryptoApi.strategyAccountDetail).not.toHaveBeenCalled()
  expect(fixtures.queryKeys.some(key => key[0] === 'crypto-legacy-account')).toBe(false)
})
