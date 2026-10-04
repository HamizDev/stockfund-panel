// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type {
  CryptoStrategyAccount,
  CryptoStrategyAccountDetail,
  CryptoStrategyDraftRequest,
  CryptoStrategyDraftResult,
} from './client'
import { StrategyAccountsPanel } from './StrategyAccountsPanel'
import { AiStrategyDraftPanel } from './AiStrategyDraftPanel'

const mocks = vi.hoisted(() => ({
  cryptoApi: {
    strategies: vi.fn(),
    strategyAccounts: vi.fn(),
    strategyAccountDetail: vi.fn(),
    createStrategyAccount: vi.fn(),
    compareStrategyAccounts: vi.fn(),
    setStrategyAccountEnabled: vi.fn(),
    runStrategyAccount: vi.fn(),
    strategyDraft: vi.fn(),
    quote: vi.fn(),
  },
  strategyAiStatus: vi.fn(),
}))

vi.mock('./client', () => ({ cryptoApi: mocks.cryptoApi }))
vi.mock('@/lib/api', () => ({ api: { strategyAiStatus: mocks.strategyAiStatus } }))
vi.mock('@/components/financials/MarkdownRenderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}))

const MODEL = {
  maintenance_margin_rate: '0.005',
  liquidation_fee_rate: '0.005',
  slippage_bps: 5,
  poll_seconds: 30,
}

function makeAccount(overrides: Partial<CryptoStrategyAccount> = {}): CryptoStrategyAccount {
  return {
    id: 'bitget-main',
    name: 'Bitget账户',
    exchange: 'bitget',
    market: 'usdm',
    symbol: 'ETHUSDT',
    strategy_id: 'ema_trend',
    strategy_params: { fast_period: 20, slow_period: 60 },
    interval: '1h',
    leverage: 10,
    initial_cash: '10000',
    allocation_pct: 10,
    stop_loss_pct: 2,
    take_profit_pct: 4,
    enabled: false,
    status: 'paused',
    last_error: null,
    last_check_ms: null,
    last_signal: null,
    last_bar_time_ms: null,
    cash: '10000',
    equity: '10100',
    total_pnl: '100',
    return_pct: 1,
    max_drawdown_pct: 2,
    fee_total: '1',
    funding_total: '0',
    liquidation_count: 0,
    trade_count: 1,
    positions: {},
    ...overrides,
  }
}

function makeDetail(account: CryptoStrategyAccount): CryptoStrategyAccountDetail {
  return { account, trades: [], nav: [{ at_ms: 1, equity: account.equity ?? '10000' }] }
}

function makeDraftResult(exchange: 'bitget' | 'binance' = 'bitget'): CryptoStrategyDraftResult {
  const draft = {
    name: `${exchange} 草案`,
    exchange,
    market: 'usdm' as const,
    symbol: 'ETHUSDT' as const,
    interval: '1h' as const,
    strategy_id: 'ema_trend' as const,
    strategy_params: { fast_period: 20, slow_period: 60 },
    leverage: 10,
    initial_cash: 10000,
    allocation_pct: 10,
    stop_loss_pct: 2,
    take_profit_pct: 4,
  }
  return {
    draft,
    rationale: '研究说明',
    risk_notes: [],
    evidence: {
      exchange,
      market: 'usdm',
      symbol: 'ETHUSDT',
      interval: '1h',
      retrieved_at_ms: 1,
      quote_asof_ms: 1,
      data_start_ms: 1,
      data_end_ms: 2,
      bars_count: 10,
      return_pct: 1,
      realized_volatility_pct: 2,
      atr_pct: 1,
      max_close_drawdown_pct: 1,
      taker_fee_rate: '0.001',
      spread_bps: 1,
      funding_rate: '0.0001',
    },
    diagnostics: { latest_signal: 'hold', signal_counts: { hold: 10 }, evaluated_bars: 10 },
    model: 'model',
    provider: 'provider',
    created_at_ms: 3,
  }
}

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  window.sessionStorage.clear()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } } })

  Object.values(mocks.cryptoApi).forEach(mock => mock.mockReset())
  mocks.strategyAiStatus.mockReset().mockResolvedValue({ configured: true, provider: 'test' })
  mocks.cryptoApi.strategies.mockResolvedValue({ strategies: [{ id: 'ema_trend', name: 'EMA 趋势' }], model: MODEL })
  mocks.cryptoApi.strategyAccounts.mockResolvedValue({ accounts: [], runtime: { running: false, poll_seconds: 30 }, model: MODEL })
  mocks.cryptoApi.strategyAccountDetail.mockImplementation(async (id: string) => makeDetail(makeAccount({ id })))
  mocks.cryptoApi.createStrategyAccount.mockImplementation(async () => ({ account: makeAccount({ id: 'created' }) }))
  mocks.cryptoApi.compareStrategyAccounts.mockResolvedValue({ accounts: [] })
  mocks.cryptoApi.setStrategyAccountEnabled.mockImplementation(async (id: string) => ({ account: makeAccount({ id }) }))
  mocks.cryptoApi.runStrategyAccount.mockImplementation(async (id: string) => ({ account: makeAccount({ id }) }))
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient.clear()
  host.remove()
})

async function render(element: ReactNode) {
  await act(async () => root.render(
    <MemoryRouter><QueryClientProvider client={queryClient}>{element}</QueryClientProvider></MemoryRouter>,
  ))
  await act(async () => { await new Promise(resolve => window.setTimeout(resolve, 0)) })
}

function buttonContaining(text: string) {
  return [...host.querySelectorAll('button')].find(button => button.textContent?.includes(text)) ?? null
}

it('keeps Binance accounts in a read-only history section and exposes chart inspection only for Bitget', async () => {
  const current = makeAccount()
  const legacy = makeAccount({ id: 'binance-old', name: 'Binance旧账户', exchange: 'binance', enabled: true, return_pct: 99 })
  mocks.cryptoApi.strategyAccounts.mockResolvedValue({ accounts: [legacy, current], runtime: { running: false, poll_seconds: 30 }, model: MODEL })
  const onInspectAccount = vi.fn()

  await render(<StrategyAccountsPanel onInspectAccount={onInspectAccount} />)

  expect(host.querySelector('select[aria-label="市场"]')).toBeNull()
  expect(host.querySelector('select[aria-label="交易所"]')).toBeNull()
  expect(host.textContent).toContain('Bitget')
  expect(host.textContent).toContain('USDT 本位合约')
  const mainBody = host.querySelector('div.overflow-x-auto table tbody')
  expect(mainBody?.textContent).toContain('#1 · Bitget账户')
  expect(mainBody?.textContent).not.toContain('Binance旧账户')

  const inspect = buttonContaining('在图表查看')
  expect(inspect).not.toBeNull()
  await act(async () => inspect?.click())
  expect(onInspectAccount).toHaveBeenCalledWith(current)

  const history = host.querySelector('details')
  expect(history?.textContent).toContain('Binance 账户只保留')
  const summary = history?.querySelector('summary')
  await act(async () => summary?.click())
  expect(history?.open).toBe(true)
  const disabledRun = [...(history?.querySelectorAll('button') ?? [])].find(button => button.textContent?.includes('运行已禁用'))
  const disabledEnable = [...(history?.querySelectorAll('button') ?? [])].find(button => button.textContent?.includes('暂停'))
  expect(disabledRun?.disabled).toBe(true)
  expect(disabledEnable?.disabled).toBe(true)
  expect(history?.textContent).toContain('不参与当前账户收益排名')
  expect(history?.querySelector('button[aria-expanded="false"]')?.textContent).toContain('明细')

  const detailButton = [...(history?.querySelectorAll('button') ?? [])].find(button => button.textContent?.includes('明细'))
  await act(async () => detailButton?.click())
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
  expect(mocks.cryptoApi.strategyAccountDetail).toHaveBeenCalledWith('binance-old')
  expect(mocks.cryptoApi.setStrategyAccountEnabled).not.toHaveBeenCalled()
  expect(mocks.cryptoApi.runStrategyAccount).not.toHaveBeenCalled()
  expect(mocks.cryptoApi.quote).not.toHaveBeenCalled()
})

it('creates AI drafts only for Bitget USDT contracts and preserves the request identity check', async () => {
  const result = makeDraftResult()
  mocks.cryptoApi.strategyDraft.mockResolvedValue(result)
  const onApply = vi.fn()
  await render(<AiStrategyDraftPanel onApply={onApply} />)

  expect(host.querySelector('select[aria-label="AI 草案市场"]')).toBeNull()
  expect(host.querySelector('select[aria-label="AI 草案交易所"]')).toBeNull()
  await act(async () => buttonContaining('生成策略草案')?.click())
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
  expect(mocks.cryptoApi.strategyDraft).toHaveBeenCalledWith({
    exchange: 'bitget', market: 'usdm', symbol: 'ETHUSDT', interval: '1h', leverage: 10, allocation_pct: 10,
  }, expect.any(AbortSignal))
  await act(async () => buttonContaining('填入账户创建表')?.click())
  expect(onApply).toHaveBeenCalledWith(result.draft)

  const mismatchedResult = makeDraftResult('binance')
  mocks.cryptoApi.strategyDraft.mockResolvedValueOnce(mismatchedResult)
  await act(async () => buttonContaining('重新生成草案')?.click())
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
  expect(host.textContent).toContain('AI 策略草案响应与请求条件不一致')
})

it('restores a Binance AI draft as view-only and cannot apply it to account creation', async () => {
  const request: CryptoStrategyDraftRequest = {
    exchange: 'binance', market: 'usdm', symbol: 'ETHUSDT', interval: '1h', leverage: 10, allocation_pct: 10,
  }
  const result = makeDraftResult('binance')
  window.sessionStorage.setItem('stockfund.crypto-paper.ai-strategy-draft.v1', JSON.stringify({ request, result }))
  const onApply = vi.fn()

  await render(<AiStrategyDraftPanel onApply={onApply} />)

  expect(host.textContent).toContain('这是恢复的 Binance 历史草案，仅供查看')
  const apply = buttonContaining('填入账户创建表')
  expect(apply?.disabled).toBe(true)
  await act(async () => apply?.click())
  expect(onApply).not.toHaveBeenCalled()
  expect(mocks.cryptoApi.strategyDraft).not.toHaveBeenCalled()
})
