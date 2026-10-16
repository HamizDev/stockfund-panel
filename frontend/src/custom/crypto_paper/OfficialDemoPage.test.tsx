// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { QK } from '@/lib/queryKeys'
import { OfficialDemoPage } from './OfficialDemoPage'
import type { DemoAccountsResult, DemoExchange, DemoSnapshot, DemoStatus } from './client'

const api = vi.hoisted(() => ({
  demoAccounts: vi.fn(),
  demoAccount: vi.fn(),
  demoConnect: vi.fn(),
  demoDisconnect: vi.fn(),
  demoCandles: vi.fn(),
  demoMarketQuote: vi.fn(),
}))
const executionApi = vi.hoisted(() => ({
  status: vi.fn(), arm: vi.fn(), plan: vi.fn(), submit: vi.fn(), reconcile: vi.fn(),
}))

vi.mock('./client', () => ({ cryptoApi: api }))
vi.mock('./demo-execution-client', () => ({ demoExecutionApi: executionApi }))
vi.mock('./OfficialDemoBotsPanel', () => ({ OfficialDemoBotsPanel: () => <div data-testid="official-demo-bots" /> }))
vi.mock('./CryptoMarketChart', () => ({
  CryptoMarketChart: ({ symbol, interval, market }: { symbol: string; interval: string; market: string }) => <div data-testid="chart" data-symbol={symbol} data-interval={interval} data-market={market} />,
}))

function status(exchange: DemoExchange, configured = false): DemoStatus {
  return {
    exchange,
    site: 'global',
    account_mode: 'classic',
    configured,
    saved: false,
    masked_key: configured ? 'demo-****1234' : '',
    state: configured ? 'connected' : 'unconfigured',
  }
}

function accounts(okxConfigured = false, bitgetConfigured = false): DemoAccountsResult {
  return { accounts: [status('okx', okxConfigured), status('bitget', bitgetConfigured)], persistence_supported: true, readonly: true }
}

function snapshot(exchange: DemoExchange, total: string | null = null): DemoSnapshot {
  return {
    exchange, site: 'global', account_mode: 'classic', environment: 'demo', readonly: true,
    observed_at: '2026-10-16T01:00:00Z', stale: false, total_equity: total, equity_ccy: 'USDT',
    balances: [], positions: [], orders: [], fills: [],
  }
}

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  localStorage.clear()
  sessionStorage.clear()
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  api.demoAccounts.mockReset().mockResolvedValue(accounts())
  api.demoAccount.mockReset().mockImplementation(async (exchange: DemoExchange) => snapshot(exchange, '125.40'))
  api.demoConnect.mockReset().mockResolvedValue({ account: status('okx', true), snapshot: snapshot('okx', '125.40') })
  api.demoDisconnect.mockReset().mockResolvedValue({ account: status('okx', false) })
  api.demoCandles.mockReset().mockImplementation(async (exchange: DemoExchange, site: string, symbol: string, interval: string) => ({ exchange, site, symbol, interval, bars: [], closed_only: true }))
  api.demoMarketQuote.mockReset().mockImplementation(async (exchange: DemoExchange, _site: string, symbol: string) => ({ exchange, symbol, bid: '—', ask: '—', mark: null, asof_ms: 0 }))
  executionApi.status.mockReset().mockResolvedValue({
    armed: false, auto: false, source_account_id: null, symbol: 'BTCUSDT', max_notional: 10,
    daily_loss_limit: 5, reason: '', managed: true, decisions: [], orders: [],
  })
  executionApi.arm.mockReset()
  executionApi.plan.mockReset()
  executionApi.submit.mockReset()
  executionApi.reconcile.mockReset()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient.clear()
  host.remove()
})

async function settle() {
  for (let index = 0; index < 6; index++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

async function render(initialEntry = '/crypto-demo?exchange=okx') {
  await act(async () => root.render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={[initialEntry]}><OfficialDemoPage /></MemoryRouter></QueryClientProvider>))
  await settle()
}

async function setInput(label: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)
  if (!input) throw new Error(`missing input ${label}`)
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    setter?.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

async function clickButton(text: string) {
  const button = [...host.querySelectorAll('button')].find(item => item.textContent?.trim() === text)
  if (!button) throw new Error(`missing button ${text}`)
  await act(async () => { button.click(); await new Promise(resolve => setTimeout(resolve, 0)) })
}

it('keeps account tables read-only and does not request private account or execution state before configuration', async () => {
  await render()

  expect(host.textContent).toContain('账户表格只读；模拟交易控制独立且默认关闭。')
  expect(host.textContent).toContain('不会自动连接、启用或修改已有本地策略账户。')
  expect(host.textContent).not.toContain('此页没有下单或自动交易功能')
  expect(api.demoAccount).not.toHaveBeenCalled()
  expect(executionApi.status).not.toHaveBeenCalled()
  expect(api.demoMarketQuote).toHaveBeenCalled()
  expect(api.demoCandles).toHaveBeenCalled()
  expect(host.querySelector('[data-testid="chart"]')?.getAttribute('data-market')).toBe('usdm')
  expect(host.querySelector<HTMLInputElement>('[aria-label="API Key"]')?.type).toBe('password')
  expect(host.querySelector<HTMLInputElement>('[aria-label="Secret"]')?.type).toBe('password')
  expect(host.querySelector<HTMLInputElement>('[aria-label="Passphrase"]')?.type).toBe('password')
  expect(host.querySelector<HTMLInputElement>('[aria-label="在本机加密保存密钥"]')?.checked).toBe(false)
  expect(host.textContent).toContain('会改用本机会话连接并替换该交易所之前保存的 Key')
  expect(host.textContent).toContain('尚未连接 OKX 官方模拟账户')
  expect(host.querySelector<HTMLAnchorElement>('a[href="https://www.okx.com/docs-v5/en/"]')).not.toBeNull()
  await setInput('API Key', 'cancel-key-private')
  await clickButton('取消并清空')
  expect(host.querySelector<HTMLInputElement>('[aria-label="API Key"]')?.value).toBe('')
})

it('clears all credentials on successful connect and keeps them out of browser storage and query state', async () => {
  await render()
  await setInput('API Key', 'demo-key-private')
  await setInput('Secret', 'demo-secret-private')
  await setInput('Passphrase', 'demo-passphrase-private')
  const save = host.querySelector<HTMLInputElement>('[aria-label="在本机加密保存密钥"]')!
  await act(async () => save.click())
  let finishConnect: ((value: { account: DemoStatus; snapshot: DemoSnapshot }) => void) | undefined
  api.demoConnect.mockImplementationOnce(() => new Promise(resolve => { finishConnect = resolve }))

  await clickButton('连接官方模拟账户')
  expect(host.querySelector<HTMLSelectElement>('[aria-label="官方模拟交易所"]')?.disabled).toBe(true)
  await act(async () => { finishConnect?.({ account: status('okx', true), snapshot: snapshot('okx', '125.40') }) })
  await settle()

  expect(api.demoConnect).toHaveBeenCalledWith('okx', {
    api_key: 'demo-key-private', secret_key: 'demo-secret-private', passphrase: 'demo-passphrase-private',
    site: 'global', account_mode: 'classic', save: true,
  })
  for (const label of ['API Key', 'Secret', 'Passphrase']) {
    expect(host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)).toBeNull()
  }
  const cachedValues = JSON.stringify(queryClient.getQueryCache().getAll().map(query => query.state.data))
  expect(cachedValues).not.toContain('demo-key-private')
  expect(cachedValues).not.toContain('demo-secret-private')
  expect(cachedValues).not.toContain('demo-passphrase-private')
  expect(queryClient.getMutationCache().getAll()).toHaveLength(0)
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
  expect(host.textContent).toContain('125.40')

  await clickButton('断开本机连接')
  expect(host.textContent).toContain('不会撤销交易所 API Key')
  await clickButton('确认断开')
  await settle()
  expect(api.demoDisconnect).toHaveBeenCalledWith('okx')
  for (const label of ['API Key', 'Secret', 'Passphrase']) {
    expect(host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)?.value).toBe('')
  }
})

it('clears credentials after a failed connect and never renders server error text that could echo a key', async () => {
  await render()
  await setInput('API Key', 'failed-key-private')
  await setInput('Secret', 'failed-secret-private')
  await setInput('Passphrase', 'failed-passphrase-private')
  api.demoConnect.mockRejectedValueOnce(new Error('failed-key-private failed-secret-private'))

  await clickButton('连接官方模拟账户')
  await settle()

  expect(host.textContent).toContain('未知错误类别')
  expect(host.textContent).not.toContain('failed-key-private')
  expect(host.textContent).not.toContain('failed-secret-private')
  expect(host.querySelector<HTMLInputElement>('[aria-label="API Key"]')?.value).toBe('')
  expect(host.querySelector<HTMLInputElement>('[aria-label="Secret"]')?.value).toBe('')
  expect(host.querySelector<HTMLInputElement>('[aria-label="Passphrase"]')?.value).toBe('')
})

it('starts a direct Bitget page in recommended UTA mode and keeps Classic manually selectable', async () => {
  await render('/crypto-demo?exchange=bitget')

  const accountMode = host.querySelector<HTMLSelectElement>('[aria-label="Bitget 账户模式"]')!
  expect(accountMode.value).toBe('uta')
  expect(host.textContent).toContain('新连接默认推荐 UTA')
  expect(host.querySelector<HTMLAnchorElement>('a[href="https://www.bitget.com/docs/uta/demo-trading/rest-api"]')).not.toBeNull()

  await act(async () => { accountMode.value = 'classic'; accountMode.dispatchEvent(new Event('change', { bubbles: true })) })
  expect(accountMode.value).toBe('classic')
  expect(host.querySelector<HTMLAnchorElement>('a[href="https://www.bitget.com/docs/classic/demo-trading/rest-api"]')).not.toBeNull()
})

it('resets a disconnected Bitget connection to the recommended UTA mode', async () => {
  await render('/crypto-demo?exchange=bitget')
  api.demoConnect.mockResolvedValueOnce({
    account: { ...status('bitget', true), account_mode: 'uta' },
    snapshot: { ...snapshot('bitget'), account_mode: 'uta' },
  })
  await setInput('API Key', 'synthetic-key-for-disconnect-test')
  await setInput('Secret', 'synthetic-secret-for-disconnect-test')
  await setInput('Passphrase', 'synthetic-passphrase-for-disconnect-test')
  await clickButton('连接官方模拟账户')
  await settle()
  expect(host.querySelector<HTMLSelectElement>('[aria-label="Bitget 账户模式"]')).toBeNull()

  await clickButton('断开本机连接')
  await clickButton('确认断开')
  await settle()

  expect(host.querySelector<HTMLSelectElement>('[aria-label="Bitget 账户模式"]')?.value).toBe('uta')
})

it('shows a whitelisted numeric error for the attempted Bitget mode without echoing server detail', async () => {
  await render()
  const exchange = host.querySelector<HTMLSelectElement>('[aria-label="官方模拟交易所"]')!
  await act(async () => { exchange.value = 'bitget'; exchange.dispatchEvent(new Event('change', { bubbles: true })) })
  await settle()
  await setInput('API Key', 'synthetic-key-for-ui-test')
  await setInput('Secret', 'synthetic-secret-for-ui-test')
  await setInput('Passphrase', 'synthetic-passphrase-for-ui-test')
  const rawDetail = '官方模拟鉴权或权限检查失败（代码 40014），请核对模拟盘专用密钥、账户类型和站点'
  api.demoConnect.mockRejectedValueOnce(new Error(`${rawDetail} [demo-error:exchange_code:40014]`))

  await clickButton('连接官方模拟账户')
  await settle()

  expect(api.demoConnect).toHaveBeenCalledWith('bitget', expect.objectContaining({ account_mode: 'uta' }))
  expect(host.textContent).toContain('Bitget UTA')
  expect(host.textContent).toContain('交易所错误码 40014')
  expect(host.textContent).toContain('不会自动尝试另一账户模式')
  expect(host.textContent).not.toContain(rawDetail)
  expect(host.textContent).not.toContain('synthetic-key-for-ui-test')
  for (const label of ['API Key', 'Secret', 'Passphrase']) {
    expect(host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)?.value).toBe('')
  }
})

it('clears credential inputs when switching exchange and does not show a cached snapshot for an unconfigured account', async () => {
  queryClient.setQueryData(QK.demoSnapshot('okx', 'global'), snapshot('okx', '998877'))
  await render()
  await setInput('API Key', 'switch-key-private')
  await setInput('Secret', 'switch-secret-private')
  await setInput('Passphrase', 'switch-passphrase-private')

  const exchange = host.querySelector<HTMLSelectElement>('[aria-label="官方模拟交易所"]')!
  await act(async () => { exchange.value = 'bitget'; exchange.dispatchEvent(new Event('change', { bubbles: true })) })
  await settle()

  expect(host.querySelector<HTMLSelectElement>('[aria-label="官方模拟交易所"]')?.value).toBe('bitget')
  expect(host.textContent).toContain('尚未连接 Bitget 官方模拟账户')
  expect(host.textContent).not.toContain('998877')
  for (const label of ['API Key', 'Secret', 'Passphrase']) {
    expect(host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)?.value).toBe('')
  }
  expect(api.demoAccount).not.toHaveBeenCalled()
  expect(queryClient.getQueryCache().getAll().map(query => query.queryKey)).toContainEqual(QK.demoCandles('bitget', 'global', 'BTCUSDT', '1h'))
  expect(host.querySelector<HTMLAnchorElement>('a[href="https://www.bitget.com/docs/uta/demo-trading/rest-api"]')).not.toBeNull()

  const accountMode = host.querySelector<HTMLSelectElement>('[aria-label="Bitget 账户模式"]')!
  expect(accountMode.value).toBe('uta')
  await act(async () => { accountMode.value = 'classic'; accountMode.dispatchEvent(new Event('change', { bubbles: true })) })
  expect(host.querySelector<HTMLAnchorElement>('a[href="https://www.bitget.com/docs/classic/demo-trading/rest-api"]')).not.toBeNull()
})

it('shows a stale snapshot as stale after a refresh fails', async () => {
  api.demoAccounts.mockResolvedValue(accounts(true, false))
  api.demoAccount.mockRejectedValueOnce(new Error('private refresh failed'))
  queryClient.setQueryData(QK.demoSnapshot('okx', 'global'), snapshot('okx', '550.00'))

  await render()

  expect(host.textContent).toContain('账户刷新失败，当前展示的是最近一次成功快照。')
  expect(host.textContent).toContain('550.00')
})

it('does not render server error details embedded in a stale snapshot', async () => {
  api.demoAccounts.mockResolvedValue(accounts(false, true))
  const serverDetail = 'ARBITRARY_SERVER_MESSAGE_PRIVATE_KEY_DO_NOT_RENDER'
  api.demoAccount.mockResolvedValueOnce({ ...snapshot('bitget'), stale: true, error: serverDetail })

  await render('/crypto-demo?exchange=bitget')

  expect(host.textContent).toContain('原始错误详情已屏蔽')
  expect(host.textContent).not.toContain(serverDetail)
})
