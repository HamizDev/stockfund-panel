// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { QK } from '@/lib/queryKeys'
import { cryptoApi, type CryptoStrategyAccount, type DemoExchange } from './client'
import { demoExecutionApi, type DemoTradingOrder, type DemoTradingPlan, type DemoTradingStatus } from './demo-execution-client'
import { OfficialDemoTradingPanel } from './OfficialDemoTradingPanel'

const execution = vi.hoisted(() => ({ status: vi.fn(), arm: vi.fn(), plan: vi.fn(), submit: vi.fn(), reconcile: vi.fn() }))
const client = vi.hoisted(() => ({ strategyAccounts: vi.fn() }))
vi.mock('./demo-execution-client', () => ({ demoExecutionApi: execution }))
vi.mock('./client', () => ({ cryptoApi: client }))

function tradingStatus(overrides: Partial<DemoTradingStatus> = {}): DemoTradingStatus {
  return {
    armed: false, auto: false, source_account_id: null, symbol: 'BTCUSDT', max_notional: 10,
    daily_loss_limit: 5, reason: '', managed: true, decisions: [], orders: [], ...overrides,
  }
}

function planned(overrides: Partial<DemoTradingPlan> = {}): DemoTradingPlan {
  return {
    id: 'plan-1', expires_at_ms: Date.now() + 60_000, symbol: 'BTCUSDT', action: 'open_long',
    quantity: '0.001', quantity_unit: 'BTC', estimated_notional: 10, price: '10000',
    reduce_only: false, leverage: 1, margin_mode: 'isolated', warnings: [], ...overrides,
  }
}

function order(state: DemoTradingOrder['state']): DemoTradingOrder {
  return {
    client_id: 'client-1', order_id: null, state, symbol: 'BTCUSDT', action: 'open_long',
    quantity: '0.001', estimated_notional: 10, created_at_ms: Date.now(),
  }
}

function sourceAccount(): CryptoStrategyAccount {
  return {
    id: 'local-account-1', name: '本地 BTC 策略', exchange: 'bitget', market: 'usdm', symbol: 'BTCUSDT',
    strategy_id: 'ema_trend', interval: '1h', leverage: 1, initial_cash: '10000', allocation_pct: 10,
    stop_loss_pct: 2, take_profit_pct: 4, enabled: true, status: 'running', last_error: null,
    last_check_ms: null, last_signal: null, last_bar_time_ms: null, cash: '10000', equity: '10000',
    total_pnl: null, return_pct: null, max_drawdown_pct: null, fee_total: '0', funding_total: '0',
    liquidation_count: 0, trade_count: 0, positions: {},
  }
}

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const initial = tradingStatus()
  vi.mocked(demoExecutionApi.status).mockReset().mockImplementation(async () => ({ ...initial }))
  vi.mocked(demoExecutionApi.arm).mockReset().mockImplementation(async (_exchange, body) => tradingStatus({
    armed: body.enabled, auto: body.source_account_id !== null, source_account_id: body.source_account_id,
    symbol: body.symbol, max_notional: body.max_notional, daily_loss_limit: body.daily_loss_limit,
  }))
  vi.mocked(demoExecutionApi.plan).mockReset().mockImplementation(async (_exchange, body) => planned({ symbol: body.symbol, action: body.action, estimated_notional: body.notional }))
  vi.mocked(demoExecutionApi.submit).mockReset().mockResolvedValue({ order: order('confirmed') })
  vi.mocked(demoExecutionApi.reconcile).mockReset().mockResolvedValue({ order: order('confirmed') })
  vi.mocked(cryptoApi.strategyAccounts).mockReset().mockResolvedValue({
    accounts: [sourceAccount()], model: {} as never, runtime: { running: true, poll_seconds: 30 },
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient.clear()
  host.remove()
})

async function settle() {
  for (let index = 0; index < 6; index++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

async function render(exchange: DemoExchange = 'bitget', configured = true) {
  await act(async () => root.render(<QueryClientProvider client={queryClient}><OfficialDemoTradingPanel exchange={exchange} configured={configured} /></QueryClientProvider>))
  await settle()
}

async function clickButton(label: string) {
  const button = [...host.querySelectorAll('button')].find(item => item.textContent?.trim() === label)
  if (!button) throw new Error(`找不到按钮：${label}`)
  await act(async () => { button.click(); await new Promise(resolve => setTimeout(resolve, 0)) })
}

async function setInput(label: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)
  if (!input) throw new Error(`找不到输入项：${label}`)
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

async function setSelect(label: string, value: string) {
  const select = host.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)
  if (!select) throw new Error(`找不到选择项：${label}`)
  await act(async () => { select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })) })
}

async function check(label: string) {
  const checkbox = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)
  if (!checkbox) throw new Error(`找不到复选框：${label}`)
  await act(async () => checkbox.click())
}

it('does not fetch private trading state or show enabled controls until the official account is connected', async () => {
  await render('okx', false)
  expect(demoExecutionApi.status).not.toHaveBeenCalled()
  expect(demoExecutionApi.plan).not.toHaveBeenCalled()
  expect(host.textContent).toContain('先连接当前交易所的官方模拟账户')
  expect([...host.querySelectorAll('button')].some(button => button.textContent?.includes('暂停模拟执行'))).toBe(false)
  expect(queryClient.getQueryCache().getAll().map(query => query.queryKey)).not.toContainEqual(QK.demoTrading('okx'))
})

it('requires explicit demo confirmation and sends the bounded arm payload with a copied rule source', async () => {
  await render()
  const enable = [...host.querySelectorAll('button')].find(button => button.textContent?.includes('启用模拟执行'))!
  expect(enable.disabled).toBe(true)
  await setInput('单笔开仓估算上限', '25')
  await setInput('日亏停止阈值', '4')
  await check('自动绑定本地策略规则')
  await settle()
  expect(cryptoApi.strategyAccounts).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('反向信号先平仓、下根 K 线再评估')
  const sourceSelect = host.querySelector<HTMLSelectElement>('[aria-label="自动规则来源账户"]')!
  expect(sourceSelect.value).toBe('')
  expect([...host.querySelectorAll('button')].find(button => button.textContent?.includes('启用模拟执行'))?.disabled).toBe(true)
  await setSelect('自动规则来源账户', 'local-account-1')
  await check('确认官方模拟执行')
  await clickButton('启用模拟执行')

  expect(demoExecutionApi.arm).toHaveBeenCalledWith('bitget', {
    enabled: true, confirm_demo: true, symbol: 'BTCUSDT', source_account_id: 'local-account-1',
    max_notional: 25, daily_loss_limit: 4,
  })
  expect(cryptoApi.strategyAccounts).toHaveBeenCalledTimes(1)
})

it('blocks confirmation after a plan expires', async () => {
  vi.mocked(demoExecutionApi.status).mockResolvedValue(tradingStatus({ armed: true }))
  vi.mocked(demoExecutionApi.plan).mockResolvedValue(planned({ expires_at_ms: Date.now() - 100 }))
  await render()
  await clickButton('预览计划')
  await settle()
  expect(host.textContent).toContain('已过期，不能提交')
  const confirm = [...host.querySelectorAll('button')].find(button => button.textContent?.includes('明确确认模拟下单'))
  expect(confirm?.disabled).toBe(true)
  expect(demoExecutionApi.submit).not.toHaveBeenCalled()
})

it('blocks plan and submission while execution is disarmed', async () => {
  await render()
  const preview = [...host.querySelectorAll('button')].find(button => button.textContent?.includes('预览计划'))!
  expect(preview.disabled).toBe(true)
  await clickButton('预览计划')
  await settle()
  expect(demoExecutionApi.plan).not.toHaveBeenCalled()
  expect(demoExecutionApi.submit).not.toHaveBeenCalled()
})

it('allows reduce-only full-position estimates above the new-open cap and accepts string estimates', async () => {
  vi.mocked(demoExecutionApi.status).mockResolvedValue(tradingStatus({ armed: true }))
  vi.mocked(demoExecutionApi.plan).mockResolvedValue(planned({ action: 'close_short', estimated_notional: '250', reduce_only: true }))
  await render()
  await setSelect('模拟计划动作', 'close_short')
  await clickButton('预览计划')
  await settle()
  expect(demoExecutionApi.plan).toHaveBeenCalledWith('bitget', { symbol: 'BTCUSDT', action: 'close_short', notional: 10 })
  expect(host.textContent).toContain('250 USDT')
  expect(host.textContent).toContain('全量平仓')
})

it('clears an unsubmitted plan and its confirmation when the exchange changes', async () => {
  vi.mocked(demoExecutionApi.status).mockImplementation(async () => tradingStatus({ armed: true }))
  await render('bitget')
  await clickButton('预览计划')
  await settle()
  expect(host.textContent).toContain('计划预览')

  await act(async () => root.render(<QueryClientProvider client={queryClient}><OfficialDemoTradingPanel exchange="okx" configured /></QueryClientProvider>))
  await settle()
  expect(host.textContent).not.toContain('计划预览')
  expect(host.textContent).toContain('OKX')
  expect(demoExecutionApi.plan).toHaveBeenCalledTimes(1)
  expect(queryClient.getQueryData(QK.demoTrading('bitget'))).toBeUndefined()
})

it('does not resubmit an uncertain plan and reconciles only by client order id', async () => {
  let statusCalls = 0
  vi.mocked(demoExecutionApi.status).mockImplementation(async () => tradingStatus({ armed: statusCalls++ === 0 }))
  vi.mocked(demoExecutionApi.submit).mockResolvedValue({ order: order('uncertain') })
  vi.mocked(demoExecutionApi.reconcile).mockResolvedValue({ order: order('confirmed') })
  await render()
  await clickButton('预览计划')
  await settle()
  await check('确认提交模拟订单')
  await clickButton('明确确认模拟下单')
  await settle()
  expect(host.textContent).toContain('结果不明')
  expect(host.textContent).toContain('结果不明，已暂停，仅查单')
  expect(queryClient.getQueryData<DemoTradingStatus>(QK.demoTrading('bitget'))?.armed).toBe(false)
  expect(host.textContent).toContain('不要重复提交')
  await clickButton('暂停模拟执行')
  expect(host.textContent).toContain('client-1')
  await clickButton('查询状态（仅对账）')
  await settle()
  expect(demoExecutionApi.submit).toHaveBeenCalledTimes(1)
  expect(demoExecutionApi.reconcile).toHaveBeenCalledWith('bitget', 'client-1')
  expect(host.textContent).toContain('已确认')
})

it('keeps pause available when status lookup fails', async () => {
  vi.mocked(demoExecutionApi.status).mockRejectedValue(new Error('status unavailable'))
  await render('okx')
  expect(host.textContent).toContain('状态读取失败')
  await clickButton('暂停模拟执行')
  expect(demoExecutionApi.arm).toHaveBeenCalledWith('okx', {
    enabled: false, confirm_demo: true, symbol: 'BTCUSDT', source_account_id: null,
    max_notional: 10, daily_loss_limit: 5,
  })
})

it('blocks plan and submission when status lookup fails', async () => {
  vi.mocked(demoExecutionApi.status).mockRejectedValue(new Error('status unavailable'))
  await render('okx')
  const preview = [...host.querySelectorAll('button')].find(button => button.textContent?.includes('预览计划'))!
  expect(preview.disabled).toBe(true)
  await clickButton('预览计划')
  await settle()
  expect(demoExecutionApi.plan).not.toHaveBeenCalled()
  expect(demoExecutionApi.submit).not.toHaveBeenCalled()
})

it('keeps durable pending orders queryable and blocks new plans after reload', async () => {
  vi.mocked(demoExecutionApi.status).mockResolvedValue(tradingStatus({ orders: [order('pending')] }))
  await render()
  await clickButton('查询状态（仅对账）')
  expect(demoExecutionApi.reconcile).toHaveBeenCalledWith('bitget', 'client-1')
  expect(demoExecutionApi.submit).not.toHaveBeenCalled()
})

it('allows pause during a slow enable and does not apply the late enable response', async () => {
  let finish: (value: DemoTradingStatus) => void = () => {}
  vi.mocked(demoExecutionApi.arm).mockImplementation(async (_exchange, body) => body.enabled
    ? await new Promise<DemoTradingStatus>(resolve => { finish = resolve }) : tradingStatus())
  await render()
  await check('确认官方模拟执行')
  await clickButton('启用模拟执行')
  await clickButton('暂停模拟执行')
  await act(async () => finish(tradingStatus({ armed: true })))
  await settle()
  expect(demoExecutionApi.arm).toHaveBeenCalledTimes(2)
  expect(queryClient.getQueryData<DemoTradingStatus>(QK.demoTrading('bitget'))?.armed).toBe(false)
})
