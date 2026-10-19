// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, useSearchParams } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StockAnalysis } from './StockAnalysis'

const mocks = vi.hoisted(() => ({
  instrumentSearch: vi.fn(),
  instrumentNames: vi.fn(),
  stockAnalysisSnapshot: vi.fn(),
  loadHistory: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ api: {
  instrumentSearch: mocks.instrumentSearch,
  instrumentNames: mocks.instrumentNames,
  stockAnalysisSnapshot: mocks.stockAnalysisSnapshot,
} }))
vi.mock('@/lib/stockAnalysisStore', () => ({
  startAnalysis: vi.fn(),
  findTodayReport: vi.fn(),
  useHistoryReports: () => ({ reports: [], loaded: true }),
  deleteReport: vi.fn(),
  openHistoryReport: vi.fn(),
  loadHistory: mocks.loadHistory,
}))
vi.mock('@/components/financials/StockFinancialSearch', () => ({
  StockFinancialSearch: ({ onSelect }: { onSelect: (symbol: string, name: string, assetType?: string) => void }) => <div>
    <button type="button" data-testid="select-etf" onClick={() => onSelect('510300.SH', '沪深300ETF', 'etf')}>选择ETF</button>
    <button type="button" data-testid="select-untyped" onClick={() => onSelect('000001.SH', '上证指数')}>选择无类型标的</button>
  </div>,
}))
vi.mock('@/components/PageHeader', () => ({ PageHeader: ({ right }: { right?: ReactNode }) => <header>{right}</header> }))
vi.mock('@/custom/global_markets/MarketRegionTabs', () => ({ MarketRegionTabs: () => null }))
vi.mock('@/components/LastStockChip', () => ({ LastStockChip: () => null }))
vi.mock('@/components/EmptyState', () => ({ EmptyState: () => null }))
vi.mock('@/components/StockPreviewDialog', () => ({ StockPreviewDialog: () => null }))
vi.mock('@/components/stock-analysis/AnalysisKChart', () => ({ AnalysisKChart: () => null }))
vi.mock('@/components/stock-analysis/AnalysisMinutePanel', () => ({
  AnalysisMinutePanel: ({ assetType }: { assetType?: string }) => <div data-testid="minute-panel" data-asset={assetType ?? ''} />,
}))
vi.mock('@/components/stock-analysis/EtfSharesPanel', () => ({ EtfSharesPanel: () => null }))
vi.mock('@/components/stock-analysis/PriceAlertDialog', () => ({ PriceAlertDialog: () => null }))
vi.mock('@/components/research/TechnicalSummary', () => ({ TechnicalSummary: () => null }))

function LocationProbe() {
  const [params] = useSearchParams()
  return <output data-testid="params">{params.toString()}</output>
}

let host: HTMLDivElement
let root: Root
let query: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  localStorage.clear()
  sessionStorage.clear()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  query = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  mocks.instrumentSearch.mockReset().mockImplementation(async (code: string) => ({ results: [
    { symbol: `${code}.SH`, name: '上证指数', asset_type: 'index' },
  ] }))
  mocks.instrumentNames.mockReset().mockResolvedValue({ names: {} })
  mocks.stockAnalysisSnapshot.mockReset().mockImplementation(async (symbol: string) => ({
    symbol,
    rows: [{ date: '2026-10-16', open: 1, high: 1.1, low: .9, close: 1.05, volume: 100 }],
    analysis: { symbol, close: 1.05, levels: {} },
    as_of: '2026-10-16',
    source: 'online',
    price_basis: 'unadjusted',
  }))
  mocks.loadHistory.mockReset().mockResolvedValue(undefined)
})

afterEach(async () => {
  await act(async () => root.unmount())
  query.clear()
  host.remove()
  localStorage.clear()
  sessionStorage.clear()
})

async function renderPage(initialEntry = '/') {
  await act(async () => root.render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <QueryClientProvider client={query}><StockAnalysis /><LocationProbe /></QueryClientProvider>
    </MemoryRouter>,
  ))
  for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

async function click(testId: string) {
  await act(async () => host.querySelector<HTMLButtonElement>(`[data-testid="${testId}"]`)!.click())
  for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

it('keeps selected type on its symbol and skips metadata/name lookups when both are known', async () => {
  await renderPage()
  await click('select-etf')

  expect(host.querySelector<HTMLElement>('[data-testid="minute-panel"]')?.dataset.asset).toBe('etf')
  expect(new URLSearchParams(host.querySelector('[data-testid="params"]')?.textContent ?? '').get('asset_type')).toBe('etf')
  expect(mocks.instrumentSearch).not.toHaveBeenCalled()
  expect(mocks.instrumentNames).not.toHaveBeenCalled()
})

it('drops a previous symbol type and falls back to exact metadata for an untyped selection', async () => {
  await renderPage()
  await click('select-etf')
  await click('select-untyped')

  expect(host.querySelector<HTMLElement>('[data-testid="minute-panel"]')?.dataset.asset).toBe('index')
  expect(new URLSearchParams(host.querySelector('[data-testid="params"]')?.textContent ?? '').has('asset_type')).toBe(false)
  expect(mocks.instrumentSearch).toHaveBeenCalledWith('000001', 20, 'stock,etf,index')
})

it('accepts the validated type saved by a selection on a direct link', async () => {
  await renderPage('/?symbol=510300.SH&name=%E6%B2%AA%E6%B7%B1300ETF&asset_type=etf')

  expect(host.querySelector<HTMLElement>('[data-testid="minute-panel"]')?.dataset.asset).toBe('etf')
  expect(mocks.instrumentSearch).not.toHaveBeenCalled()
  expect(mocks.instrumentNames).not.toHaveBeenCalled()
})
