// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { PortfolioPanel } from './PortfolioPanel'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({ api: { paperAccounts: vi.fn(), researchPortfolio: vi.fn() } }))
vi.mock('@/components/research/ResearchAIButton', () => ({
  ResearchAIButton: ({ label, prompt, disabled }: { label: string; prompt: string; disabled?: boolean }) => (
    <button type="button" data-testid="research-ai" data-prompt={prompt} disabled={disabled}>{label}</button>
  ),
}))

const accounts = [
  { id: 'acc-a', name: '模拟账户 A', status: 'active' as const },
  { id: 'acc-b', name: '模拟账户 B', status: 'active' as const },
]

type TestPosition = {
  symbol: string
  name: string
  market: string
  currency: string
  qty: number
  price: number
  price_date: string
  industry: string
  weight: number
}

type TestPortfolioGroup = {
  currency: string
  total_value: number
  positions: TestPosition[]
  concentration: { top1: number | null; top3: number | null; hhi: number | null }
  risk: {
    annualized_volatility: number | null
    max_drawdown: number | null
    risk_contributions: Record<string, number | null>
    correlations: { symbols: string[]; values: (number | null)[][] } | null
    included_weight: number | null
    price_basis: string
    interpretation: string
  }
  observations: number | null
  coverage: Record<string, unknown>
  gaps: unknown[]
}

type TestPortfolio = { account_name: string; note: string; groups: TestPortfolioGroup[]; gaps: unknown[] }

function portfolio(accountName: string, symbols: string[], note = '只读模拟数据'): TestPortfolio {
  const positions = symbols.map((symbol, index) => ({
    symbol,
    name: `${symbol} 名称`,
    market: 'CN',
    currency: 'CNY',
    qty: 1,
    price: 100,
    price_date: '2025-01-03',
    industry: index === 0 ? '未分类' : '示例行业',
    weight: 1 / symbols.length,
  }))
  const correlationSymbols = positions.map(position => position.symbol)
  return {
    account_name: accountName,
    note,
    groups: symbols.length ? [{
      currency: 'CNY',
      total_value: symbols.length * 100,
      positions,
      concentration: { top1: 1 / symbols.length, top3: 1, hhi: 1 / symbols.length },
      risk: {
        annualized_volatility: 0,
        max_drawdown: 0,
        risk_contributions: Object.fromEntries(symbols.map(symbol => [symbol, 1 / symbols.length])),
        correlations: {
          symbols: correlationSymbols,
          values: symbols.map((_, row) => symbols.map((__, column) => row === column ? 1 : 0)),
        },
        included_weight: 1,
        price_basis: '调用方提供的同口径历史复权收盘价',
        interpretation: '当前持仓权重的静态历史压力试算，不代表账户真实净值',
      },
      observations: 0,
      coverage: {
        position_count: positions.length,
        history_count: positions.length,
        included_weight: 1,
        included_symbols: symbols,
        common_price_dates: 1,
        first_common_date: '2025-01-01',
        last_common_date: '2025-01-01',
      },
      gaps: [],
    }] : [],
    gaps: [],
  }
}

function emptyPortfolio(accountName: string) {
  return { account_name: accountName, note: '没有持仓', groups: [], gaps: [] }
}

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  sessionStorage.clear()
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.resetAllMocks()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  vi.mocked(api.paperAccounts).mockResolvedValue({ accounts } as never)
  vi.mocked(api.researchPortfolio).mockImplementation(async accountId => portfolio(
    accountId === 'acc-a' ? '模拟账户 A' : '模拟账户 B',
    [accountId === 'acc-a' ? 'AAA' : 'BBB'],
  ) as never)
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
})

async function settle() {
  for (let index = 0; index < 5; index++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

async function renderPanel() {
  await act(async () => root.render(<QueryClientProvider client={client}><PortfolioPanel /></QueryClientProvider>))
  await settle()
}

async function selectAccount(id: string) {
  const select = host.querySelector<HTMLSelectElement>('[aria-label="选择模拟账户"]')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, id)
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

function button(label: string): HTMLButtonElement {
  return [...host.querySelectorAll('button')].find(candidate => candidate.textContent?.includes(label)) as HTMLButtonElement
}

it('renders zero separately from missing values and labels the static risk basis', async () => {
  const result = portfolio('模拟账户 A', ['AAA', 'BBB'])
  const group = result.groups[0]!
  group.concentration.top1 = 0
  group.concentration.top3 = null
  group.concentration.hhi = 0
  group.risk.annualized_volatility = 0
  group.risk.max_drawdown = null
  group.risk.risk_contributions = { AAA: 0, BBB: null }
  group.risk.correlations!.values = [[1, 0], [0, null]]
  vi.mocked(api.researchPortfolio).mockResolvedValue(result as never)

  await renderPanel()

  expect(host.textContent).toContain('0.00%')
  expect(host.textContent).toContain('0.0000')
  expect(host.textContent).toContain('—')
  expect(host.textContent).toContain('未分类')
  expect(host.textContent).toContain('不代表账户真实净值')
  expect(host.textContent).toContain('AI解读持仓')
  expect(api.researchPortfolio).toHaveBeenCalledWith('acc-a', expect.any(AbortSignal))
})

it('does not show the previous account while the selected account is loading', async () => {
  let resolveB!: (data: Record<string, unknown>) => void
  vi.mocked(api.researchPortfolio).mockImplementation(accountId => accountId === 'acc-a'
    ? Promise.resolve(portfolio('模拟账户 A', ['AAA']) as unknown as Record<string, unknown>)
    : new Promise<Record<string, unknown>>(resolve => { resolveB = resolve }))

  await renderPanel()
  expect(host.textContent).toContain('AAA')

  await selectAccount('acc-b')
  expect(host.textContent).not.toContain('AAA')
  expect(host.textContent).toContain('正在读取 模拟账户 B 的持仓体检')

  await act(async () => { resolveB(portfolio('模拟账户 B', ['BBB']) as unknown as Record<string, unknown>) })
  await settle()
  expect(host.textContent).toContain('BBB')
  expect(host.textContent).not.toContain('AAA')
})

it('keeps the last good result visible during refresh and offers retry after a failed read', async () => {
  let calls = 0
  vi.mocked(api.researchPortfolio).mockImplementation(async () => {
    calls++
    if (calls === 1) return portfolio('模拟账户 A', ['AAA']) as never
    if (calls === 2) throw new Error('offline')
    return portfolio('模拟账户 A', ['AAA']) as never
  })

  await renderPanel()
  await act(async () => button('刷新').click())
  await settle()
  expect(host.textContent).toContain('AAA')
  expect(host.textContent).toContain('刷新失败，仍保留上次可用结果。')

  await act(async () => button('重试读取').click())
  await settle()
  expect(host.textContent).toContain('AAA')
  expect(host.textContent).not.toContain('刷新失败，仍保留上次可用结果。')
})

it('shows an explicit empty state for an account without holdings', async () => {
  vi.mocked(api.researchPortfolio).mockResolvedValue(emptyPortfolio('模拟账户 A') as never)

  await renderPanel()

  expect(host.textContent).toContain('当前模拟账户暂无持仓。')
  expect(host.querySelector<HTMLButtonElement>('[data-testid="research-ai"]')?.disabled).toBe(true)
})

it('keeps the AI payload structured, neutral, and within 3500 characters', async () => {
  const symbols = Array.from({ length: 30 }, (_, index) => `S${String(index).padStart(2, '0')}`)
  const result = portfolio('账户'.repeat(300), symbols, '说明'.repeat(500))
  const group = result.groups[0]!
  group.positions = group.positions.map(position => ({ ...position, name: '标的名称'.repeat(50) }))
  group.risk.correlations = {
    symbols,
    values: symbols.map((_, row) => symbols.map((__, column) => row === column ? 1 : 0.5)),
  }
  group.gaps = Array.from({ length: 20 }, (_, index) => ({ code: 'history_missing', symbol: symbols[index % symbols.length] }))
  result.gaps = group.gaps
  vi.mocked(api.researchPortfolio).mockResolvedValue(result as never)

  await renderPanel()

  const prompt = host.querySelector<HTMLButtonElement>('[data-testid="research-ai"]')?.dataset.prompt ?? ''
  expect(prompt.length).toBeLessThanOrEqual(3500)
  expect(prompt).toContain('请逐只分析本模拟账户持仓及风险')
  expect(prompt).toContain('相反证据与复核条件')
  expect(prompt).toContain('结构化')
  for (const symbol of symbols) expect(prompt).toContain(symbol)
  expect(prompt).not.toContain('买入')
  expect(prompt).not.toContain('卖出')
})
