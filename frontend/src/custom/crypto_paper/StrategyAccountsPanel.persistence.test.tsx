// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StrategyAccountsPanel } from './StrategyAccountsPanel'
import { cryptoApi, type CryptoStrategyAccount, type CryptoStrategyAccountDetail } from './client'

vi.mock('./AiStrategyDraftPanel', () => ({ AiStrategyDraftPanel: () => null }))
vi.mock('./StrategySignalPreview', () => ({ StrategySignalPreview: () => null }))
vi.mock('./client', () => ({ cryptoApi: {
  strategies: vi.fn(), strategyAccounts: vi.fn(), strategyAccountDetail: vi.fn(),
  createStrategyAccount: vi.fn(), compareStrategyAccounts: vi.fn(),
  setStrategyAccountEnabled: vi.fn(), runStrategyAccount: vi.fn(), strategyPreview: vi.fn(),
} }))

const model = { poll_seconds: 30, slippage_bps: 5, maintenance_margin_rate: '.005', liquidation_fee_rate: '.005' }
function account(id: string): CryptoStrategyAccount {
  return {
    id, name: 'Bitget 模拟账户', exchange: 'bitget', market: 'usdm', symbol: 'BTCUSDT', strategy_id: 'ema_trend',
    strategy_params: { fast_period: 20, slow_period: 60 }, interval: '1h', leverage: 1, initial_cash: '1000',
    allocation_pct: 10, stop_loss_pct: 2, take_profit_pct: 4, enabled: false, status: 'paused', last_error: null,
    last_check_ms: null, last_signal: null, last_bar_time_ms: null, cash: '1000', equity: '1000', total_pnl: '0',
    return_pct: 0, max_drawdown_pct: 0, fee_total: '0', funding_total: '0', liquidation_count: 0, trade_count: 0, positions: {},
  }
}

let host: HTMLDivElement
let root: Root
let accounts: CryptoStrategyAccount[]

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  sessionStorage.clear()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  accounts = [account('account-1')]
  vi.mocked(cryptoApi.strategies).mockResolvedValue({ model, strategies: [{ id: 'ema_trend', name: 'EMA 趋势', description: '趋势' }] })
  vi.mocked(cryptoApi.strategyAccounts).mockImplementation(async () => ({ accounts, model, runtime: { running: true, poll_seconds: 30 } }))
  vi.mocked(cryptoApi.strategyAccountDetail).mockImplementation(async (id: string) => ({
    account: account(id), trades: [], nav: [],
  } as CryptoStrategyAccountDetail))
  vi.mocked(cryptoApi.createStrategyAccount).mockReset()
  vi.mocked(cryptoApi.compareStrategyAccounts).mockReset()
  vi.mocked(cryptoApi.setStrategyAccountEnabled).mockReset()
  vi.mocked(cryptoApi.runStrategyAccount).mockReset()
  vi.mocked(cryptoApi.strategyAccountDetail).mockClear()
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

async function settle() {
  for (let index = 0; index < 5; index++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

it('restores the expanded account and fetches fresh detail from the server without account mutations', async () => {
  sessionStorage.setItem('page-view:crypto-expanded-account', JSON.stringify('account-1'))
  await act(async () => root.render(<StrategyAccountsPanel />))
  await settle()

  expect(host.querySelector('button[aria-expanded="true"]')?.textContent).toContain('收起')
  expect(cryptoApi.strategyAccountDetail).toHaveBeenCalledWith('account-1')
  expect(host.textContent).toContain('当前持仓')
  expect(cryptoApi.createStrategyAccount).not.toHaveBeenCalled()
  expect(cryptoApi.compareStrategyAccounts).not.toHaveBeenCalled()
  expect(cryptoApi.setStrategyAccountEnabled).not.toHaveBeenCalled()
  expect(cryptoApi.runStrategyAccount).not.toHaveBeenCalled()
})

it('clears a remembered expanded account when the server no longer returns it', async () => {
  sessionStorage.setItem('page-view:crypto-expanded-account', JSON.stringify('deleted-account'))
  accounts = []
  await act(async () => root.render(<StrategyAccountsPanel />))
  await settle()

  expect(sessionStorage.getItem('page-view:crypto-expanded-account')).toBe('null')
  expect(cryptoApi.strategyAccountDetail).not.toHaveBeenCalled()
})
