// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { QK } from '@/lib/queryKeys'
import type { DemoBot, DemoBotsStatus } from './demo-bots-client'
import { OfficialDemoBotsPanel } from './OfficialDemoBotsPanel'

const api = vi.hoisted(() => ({
  status: vi.fn(), createPresets: vi.fn(), setEnabled: vi.fn(), setBotEnabled: vi.fn(), reconcile: vi.fn(), preflight: vi.fn(),
}))
vi.mock('./demo-bots-client', () => ({ demoBotsApi: api }))

function bot(overrides: Partial<DemoBot> = {}): DemoBot {
  return {
    id: 'btc-breakout', name: 'BTC 区间突破 · 10U', symbol: 'BTCUSDT', strategy_id: 'channel_breakout',
    description: '已收盘15分钟K突破此前12根高低区间，顺势做多或做空。', interval: '15m',
    budget_usdt: 10, max_notional: 90, leverage: 10, enabled: false, armed: false, status: 'paused',
    reason: '已准备；连接欧易官方模拟后可启用', last_check_ms: 1_798_000_000_000,
    last_bar_ms: null, last_signal: null, position: null, position_verified: true,
    metrics: {
      closed_trades: 7, wins: 3, win_rate_pct: 42.86, realized_pnl_usdt: '0.125', fees_usdt: '0.035',
      open_pnl_usdt: null, allocation_equity_usdt: '10.125', max_drawdown_pct: 1.25, funding_included: false,
    },
    orders: [],
    ...overrides,
  }
}

function status(overrides: Partial<DemoBotsStatus> = {}): DemoBotsStatus {
  return {
    exchange: 'okx', environment: 'demo', connection_ready: true, legacy_armed: false,
    bots: [bot(), bot({ id: 'eth-trend', name: 'ETH 均线趋势 · 10U', symbol: 'ETHUSDT', interval: '5m' }),
      bot({ id: 'sol-pullback', name: 'SOL 强势回撤 · 10U', symbol: 'SOLUSDT', interval: '5m' })],
    ...overrides,
  }
}

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  vi.mocked(api.status).mockReset().mockImplementation(async () => status())
  vi.mocked(api.createPresets).mockReset().mockResolvedValue(status())
  vi.mocked(api.setEnabled).mockReset().mockResolvedValue(status())
  vi.mocked(api.setBotEnabled).mockReset().mockResolvedValue(status())
  vi.mocked(api.reconcile).mockReset().mockResolvedValue(status())
  vi.mocked(api.preflight).mockReset()
})

it('shows unknown official positions as pending and contract quantities in lots', async () => {
  vi.mocked(api.status).mockResolvedValue(status({ bots: [bot({ position_verified: false }), bot({
    id: 'eth', symbol: 'ETHUSDT', position_verified: true,
    position: { side: 'long', quantity: '0.03', entry_price: '2500', notional: '7.5' },
  })] }))
  await render()
  expect(host.textContent).toContain('官方仓位待核对')
  expect(host.textContent).toContain('0.03 张')
  expect(host.textContent).not.toContain('0.03 ETH')
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient.clear()
  host.remove()
})

async function settle() {
  for (let index = 0; index < 6; index++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

async function render() {
  await act(async () => root.render(<QueryClientProvider client={queryClient}><OfficialDemoBotsPanel /></QueryClientProvider>))
  await settle()
}

async function clickButton(label: string) {
  const button = [...host.querySelectorAll('button')].find(item => item.textContent?.includes(label))
  if (!button) throw new Error(`找不到按钮：${label}`)
  await act(async () => { button.click(); await settle() })
}

async function checkConfirmation() {
  const checkbox = host.querySelector<HTMLInputElement>('input[aria-label="确认仅使用欧易官方模拟"]')
  if (!checkbox) throw new Error('找不到模拟环境确认项')
  await act(async () => checkbox.click())
}

it('loads three independent strategy cards without creating or enabling anything', async () => {
  await render()

  expect(api.status).toHaveBeenCalledWith('okx', expect.any(AbortSignal))
  expect(api.createPresets).not.toHaveBeenCalled()
  expect(api.setEnabled).not.toHaveBeenCalled()
  expect(api.setBotEnabled).not.toHaveBeenCalled()
  expect(queryClient.getQueryCache().getAll().map(query => query.queryKey)).toContainEqual(QK.demoBots('okx'))
  expect(host.textContent).toContain('不是三个独立子账户')
  expect(host.textContent).toContain('已收盘15分钟K突破此前12根高低区间')
  expect(host.textContent).toContain('3 / 7')
  expect(host.textContent).toContain('42.86%')
  expect(host.textContent).toContain('样本较少')
  expect(host.textContent).toContain('资金费尚未分摊')
  expect(host.textContent).toContain('统计只从实际模拟成交后向前累计')
  expect(host.querySelector<HTMLInputElement>('input[aria-label="确认仅使用欧易官方模拟"]')?.checked).toBe(false)
})

it('keeps all start controls disabled until connected and explicitly confirmed', async () => {
  vi.mocked(api.status).mockResolvedValue(status({ connection_ready: false }))
  await render()
  await checkConfirmation()

  expect(host.textContent).toContain('尚未连接欧易官方模拟账户')
  expect([...host.querySelectorAll('button')].find(button => button.textContent?.includes('全部启用'))?.disabled).toBe(true)
  expect([...host.querySelectorAll('button')].find(button => button.textContent?.includes('启用此策略'))?.disabled).toBe(true)
  expect(api.setEnabled).not.toHaveBeenCalled()
  expect(api.setBotEnabled).not.toHaveBeenCalled()
})

it('requires the in-page checkbox for per-bot enable and sends demo confirmation', async () => {
  await render()
  expect([...host.querySelectorAll('button')].find(button => button.textContent?.includes('启用此策略'))?.disabled).toBe(true)
  expect(api.setBotEnabled).not.toHaveBeenCalled()

  await checkConfirmation()
  await clickButton('启用此策略（官方模拟）')

  expect(api.setBotEnabled).toHaveBeenCalledWith('okx', 'btc-breakout', { enabled: true, confirm_demo: true })
  expect(host.querySelector<HTMLInputElement>('input[aria-label="确认仅使用欧易官方模拟"]')?.checked).toBe(false)
  expect(host.textContent).toContain('已请求启动官方模拟')
})

it('allows disabling a saved strategy after restart without treating it as currently running', async () => {
  vi.mocked(api.status).mockResolvedValue(status({ bots: [bot({ enabled: true, armed: false, status: 'paused' })] }))
  await render()

  expect(host.textContent).toContain('已配置，当前未运行')
  expect([...host.querySelectorAll('button')].find(button => button.textContent?.includes('暂停此策略'))?.disabled).toBe(false)
  await clickButton('暂停此策略')

  expect(api.setBotEnabled).toHaveBeenCalledWith('okx', 'btc-breakout', { enabled: false, confirm_demo: false })
})

it('creates default-disabled presets only after the explicit button is pressed', async () => {
  vi.mocked(api.status).mockResolvedValue(status({ bots: [] }))
  await render()

  expect(api.createPresets).not.toHaveBeenCalled()
  expect(host.textContent).toContain('不会连接账户、启用策略或发出委托')
  await clickButton('创建三个默认策略预设')

  expect(api.createPresets).toHaveBeenCalledWith('okx')
  expect(api.setEnabled).not.toHaveBeenCalled()
  expect(api.setBotEnabled).not.toHaveBeenCalled()
})

it('offers only reconciliation for unresolved orders and never exposes raw failures', async () => {
  const pending = { client_id: 'client-demo-1', action: 'open_long', state: 'uncertain', created_at_ms: 1_798_000_000_000 }
  vi.mocked(api.status).mockResolvedValue(status({ bots: [bot({ orders: [pending] })] }))
  vi.mocked(api.reconcile).mockRejectedValue(new Error('secret API key diagnostic'))
  await render()

  expect(host.textContent).toContain('先核对欧易官方状态')
  await clickButton('仅对账')

  expect(api.reconcile).toHaveBeenCalledWith('okx', 'btc-breakout')
  expect(host.textContent).toContain('委托对账失败，请核对欧易官方委托并稍后重试。')
  expect(host.textContent).not.toContain('secret API key diagnostic')
  expect(api.setBotEnabled).not.toHaveBeenCalled()
})

it('shows a fixed safe message when an enable request fails', async () => {
  vi.mocked(api.setBotEnabled).mockRejectedValueOnce(new Error('raw exchange payload with secret'))
  await render()
  await checkConfirmation()
  await clickButton('启用此策略（官方模拟）')

  expect(host.textContent).toContain('策略启用失败，请检查官方模拟配置')
  expect(host.textContent).not.toContain('raw exchange payload with secret')
})

it('shows a known setup rejection instead of hiding it in a generic batch error', async () => {
  api.setEnabled.mockRejectedValueOnce(new Error('请先在OKX模拟盘设为合约账户、单向持仓模式'))
  await render()
  await checkConfirmation()
  await clickButton('全部启用（官方模拟）')
  expect(host.textContent).toContain('请先在OKX模拟盘设为合约账户、单向持仓模式')
  expect(host.textContent).not.toContain('批量启用失败')
})

it('checks configuration only on demand and displays actual mode and leverage', async () => {
  api.preflight.mockResolvedValueOnce({ exchange: 'okx', environment: 'demo', checked_at_ms: 1_798_000_000_000, ready: false,
    checks: [{ label: '持仓模式', passed: false, actual: '双向持仓', required: '单向持仓' }],
    bots: [{ id: 'btc', name: 'BTC 检查', symbol: 'BTCUSDT', checks: [{ label: '逐仓杠杆', passed: false, actual: '3 倍', required: '10 倍' }] }],
  })
  await render()
  expect(api.preflight).not.toHaveBeenCalled()
  await clickButton('检查运行条件（不下单）')
  expect(host.textContent).toContain('当前：双向持仓')
  expect(host.textContent).toContain('当前：3 倍')
  expect(host.textContent).toContain('要求：10 倍')
  expect(api.setEnabled).not.toHaveBeenCalled()
  expect(api.setBotEnabled).not.toHaveBeenCalled()
})
