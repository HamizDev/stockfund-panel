// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Financials } from './Financials'

vi.mock('@/lib/useSharedQueries', () => ({
  useCapabilities: () => ({ data: { capabilities: { financial: true } } }),
  useCapabilityMatrix: () => ({ data: null }),
}))
vi.mock('@/lib/capability-labels', () => ({ routeCapUsable: () => true }))
vi.mock('@/lib/useFinancials', () => ({
  useFinancialStatus: () => ({ data: { available: true, tables: {}, last_sync: {} }, isLoading: false }),
  useFinancialSync: () => ({ isPending: false, mutate: vi.fn() }),
}))
vi.mock('@/components/PageHeader', () => ({ PageHeader: ({ title }: { title: string }) => <h1>{title}</h1> }))
vi.mock('@/components/EmptyState', () => ({ EmptyState: ({ title }: { title: string }) => <div>{title}</div> }))
vi.mock('@/components/financials/StockFinancialSearch', () => ({ StockFinancialSearch: () => <div>股票搜索</div> }))
vi.mock('@/components/financials/StockFinancialDetail', () => ({
  StockFinancialDetail: ({ symbol, name }: { symbol: string; name: string }) => <div data-testid="financial-detail">{name}|{symbol}</div>,
}))
vi.mock('@/components/financials/ReportHistoryPanel', () => ({ ReportHistoryPanel: () => null }))
vi.mock('@/components/LastStockChip', () => ({ LastStockChip: () => null }))
vi.mock('@/components/Toast', () => ({ toast: vi.fn() }))

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  localStorage.clear()
  localStorage.setItem('last_stock:financials', JSON.stringify({ symbol: '600519.SH', name: '贵州茅台' }))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

it('opens the last viewed financial stock after the route remounts', async () => {
  await act(async () => root.render(<MemoryRouter><Financials /></MemoryRouter>))
  expect(host.querySelector('[data-testid="financial-detail"]')?.textContent).toBe('贵州茅台|600519.SH')

  await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent?.includes('清除'))!.click())
  expect(host.querySelector('[data-testid="financial-detail"]')).toBeNull()
  await act(async () => root.render(null))
  await act(async () => root.render(<MemoryRouter><Financials /></MemoryRouter>))
  expect(host.querySelector('[data-testid="financial-detail"]')).toBeNull()
  expect(localStorage.getItem('last_stock:financials')).toBeNull()
})
