// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CompanyFinancialsPanel } from './CompanyFinancialsPanel'
import type { GlobalFinancialTable, GlobalFinancials } from './types'

const fixtures = vi.hoisted(() => ({ financials: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { globalFinancials: fixtures.financials } }))

const response = (symbol = '00700.HK', table: GlobalFinancialTable = 'metrics'): GlobalFinancials => ({
  market: 'hk', symbol, table, currency: 'CNY', source: 'eastmoney_f10',
  fetched_at_ms: Date.now(), cached: false, stale: false, warnings: [],
  items: [{ period_end: '2026-06-30', published_at: '2026-08-15', currency: 'CNY',
    accounting_standard: null, values: [
      { key: 'revenue', label: '营业收入', value: 100_000_000, unit: '元' },
      { key: 'roe', label: '净资产收益率', value: 12.3, unit: '%' },
      { key: 'debt', label: '缺失项目', value: null, unit: '%' },
    ] }],
})

let host: HTMLDivElement
let root: Root
let client: QueryClient
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixtures.financials.mockReset().mockImplementation(async (_market, symbol, table) => response(symbol, table))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})
afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
})
async function settle() {
  for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}
async function mount(symbol = '00700.HK', kind: 'stock' | 'etf' | 'index' = 'stock') {
  await act(async () => root.render(<QueryClientProvider client={client}>
    <CompanyFinancialsPanel key={symbol} market="hk" symbol={symbol} kind={kind} />
  </QueryClientProvider>))
  await settle()
}
async function click(text: string) {
  const button = [...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === text)
  expect(button).toBeDefined()
  await act(async () => button!.click())
  await settle()
}

it('requests only after opening and retains report currency, missing values and percentage units', async () => {
  await mount()
  expect(fixtures.financials).not.toHaveBeenCalled()
  await click('查看财务')
  expect(fixtures.financials).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('报表货币：CNY')
  expect(host.textContent).toContain('12.3 %')
  expect(host.textContent).toContain('缺失项目—')
  expect(host.textContent).toContain('公告日：2026-08-15')
  await click('收起财务')
  await click('查看缓存财务')
  expect(fixtures.financials).toHaveBeenCalledTimes(1)
})

it('separates tables and retains cached results after leaving and returning', async () => {
  await mount()
  await click('查看财务')
  await click('现金流')
  expect(fixtures.financials).toHaveBeenCalledTimes(2)
  await act(async () => root.render(null))
  await mount()
  await click('查看缓存财务')
  expect(host.textContent).toContain('报告期 2026-06-30')
  expect(fixtures.financials).toHaveBeenCalledTimes(2)
})

it('does not request company statements for ETFs or indices', async () => {
  await mount('02800.HK', 'etf')
  expect(host.textContent).toContain('ETF 不适用上市公司财务报表。')
  await mount('^HSI', 'index')
  expect(host.textContent).toContain('指数不适用上市公司财务报表。')
  expect(fixtures.financials).not.toHaveBeenCalled()
})

it('does not display a result from a different instrument', async () => {
  fixtures.financials.mockResolvedValue(response('09988.HK'))
  await mount()
  await click('查看财务')
  expect(host.textContent).not.toContain('报告期 2026-06-30')
})

it('shows a safe error and recovers by retrying without clearing the main chart', async () => {
  fixtures.financials.mockRejectedValueOnce(new Error('暂时不可用'))
  await mount()
  await click('查看财务')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('暂时不可用')
  await click('重试')
  expect(host.textContent).toContain('报告期 2026-06-30')
})
