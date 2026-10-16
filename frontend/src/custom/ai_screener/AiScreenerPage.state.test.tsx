// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { Candidate, CandidateResponse } from './client'
import { AiScreenerPage } from './AiScreenerPage'

const fixtures = vi.hoisted(() => ({
  calls: [] as string[],
  getCandidates: vi.fn(),
  resolveEtf: null as ((value: CandidateResponse) => void) | null,
  generate: vi.fn(),
}))

vi.mock('@tanstack/react-query', async () => {
  const React = await import('react')
  type QueryOptions = { queryKey: unknown[]; queryFn: (context: { signal: AbortSignal }) => unknown; enabled?: boolean }
  return {
    QueryClientProvider: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
    useQuery: (options: QueryOptions) => {
      const key = JSON.stringify(options.queryKey)
      const enabled = options.enabled ?? true
      const [requestId, setRequestId] = React.useState(0)
      const [state, setState] = React.useState({
        data: undefined as unknown, isLoading: enabled, isFetching: enabled,
        isError: false, isSuccess: false, error: null as unknown,
      })
      const latest = React.useRef(options)
      latest.current = options
      React.useEffect(() => {
        if (!enabled) {
          setState({ data: undefined, isLoading: false, isFetching: false, isError: false, isSuccess: false, error: null })
          return
        }
        let active = true
        const controller = new AbortController()
        setState(current => ({ ...current, isLoading: current.data === undefined, isFetching: true, isError: false, isSuccess: false, error: null }))
        Promise.resolve(latest.current.queryFn({ signal: controller.signal })).then(data => {
          if (active) setState({ data, isLoading: false, isFetching: false, isError: false, isSuccess: true, error: null })
        }).catch(error => {
          if (active) setState({ data: undefined, isLoading: false, isFetching: false, isError: true, isSuccess: false, error })
        })
        return () => { active = false; controller.abort() }
      }, [key, enabled, requestId])
      return { ...state, refetch: () => { setRequestId(current => current + 1); return Promise.resolve() } }
    },
  }
})

vi.mock('./client', () => ({
  getCandidates: (assetType: string) => fixtures.getCandidates(assetType),
  formatMoney: (value: number | null) => value == null ? '—' : String(value),
  formatPct: (value: number | null) => value == null ? '—' : `${value}%`,
}))
vi.mock('../assistant/client', () => ({ fetchAssistantStatus: async () => ({ configured: false }) }))
vi.mock('./StockCandidateCard', () => ({
  StockCandidateCard: ({ item, selected, onSelect }: {
    item: { symbol: string; name: string }; selected: boolean; onSelect: () => void
  }) => <button type="button" data-candidate={item.symbol} aria-pressed={selected} onClick={onSelect}>{item.name}</button>,
}))
vi.mock('./CandidateAnalysisPanel', () => ({ CandidateAnalysisPanel: () => null }))
vi.mock('./useCandidateResearch', () => ({
  researchKey: (asset: string, symbol: string) => `${asset}:${symbol}`,
  useCandidateResearch: () => ({ runningKey: null, drafts: {}, reports: {}, generate: fixtures.generate }),
}))
vi.mock('@/components/StockPreviewDialog', () => ({ StockPreviewDialog: () => null }))
vi.mock('@/components/PageHeader', () => ({ PageHeader: ({ title }: { title: string }) => <header>{title}</header> }))

function candidate(symbol: string, name: string, hitCount = 2): Candidate {
  return {
    symbol,
    name,
    strategies: [{ id: 'trend', name: '趋势', description: 'fixture' }],
    hit_count: hitCount,
    metrics: {
      close: 10, change_pct: 0.01, amount: 1_000_000, volume: 100_000,
      turnover_rate: 1, pe_ttm: 10, pb: 1, ma5: 10, ma20: 9.8, ma60: 9.5,
    },
    price_source: 'daily',
    price_basis: 'qfq',
  }
}

function response(assetType: 'stock' | 'etf', items: Candidate[]): CandidateResponse {
  return {
    asset_type: assetType, as_of: '2026-10-15', updated_at: 1, cache_available: true,
    coverage: { computed: items.length, total: items.length },
    quote: { live: false, provider: null, last_fetch_ms: null, age_ms: null },
    total: items.length, items,
  }
}

const etfResponse = response('etf', [candidate('510300.SH', '沪深300 ETF'), candidate('159934.SZ', '黄金 ETF', 1)])
const stockResponse = response('stock', [candidate('600000.SH', '浦发银行'), candidate('000001.SZ', '平安银行', 1)])

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  sessionStorage.clear()
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixtures.calls.length = 0
  fixtures.resolveEtf = null
  fixtures.generate.mockClear()
  fixtures.getCandidates.mockReset()
  fixtures.getCandidates.mockImplementation((assetType: string) => {
    fixtures.calls.push(assetType)
    if (assetType === 'etf' && fixtures.calls.filter(call => call === 'etf').length === 1) {
      return new Promise<CandidateResponse>(resolve => { fixtures.resolveEtf = resolve })
    }
    if (assetType === 'etf') return Promise.resolve(etfResponse)
    return Promise.resolve(stockResponse)
  })
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(() => ({ matches: false, media: '', onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
  })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

async function settle() {
  for (let index = 0; index < 5; index++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

function viewState() {
  return JSON.parse(sessionStorage.getItem('page-view:ai-screener') || 'null') as Record<string, unknown>
}

async function changeSearch(value: string) {
  const input = host.querySelector<HTMLInputElement>('input[aria-label="搜索候选"]')!
  const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    valueSetter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await settle()
}

function render(initialPath: string) {
  return root.render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/ai-screener" element={<AiScreenerPage />} />
        <Route path="/away" element={<Link to="/ai-screener">返回选股</Link>} />
      </Routes>
      <Link to="/away">离开选股</Link>
    </MemoryRouter>,
  )
}

it('keeps the saved ETF selection while its query is pending, isolates per-asset state, and restores it after navigation', async () => {
  sessionStorage.setItem('page-view:ai-screener', JSON.stringify({
    search: '沪深300', multiOnly: true,
    selectedByAsset: { stock: '600000.SH', etf: '510300.SH' },
    visibleLimitByAsset: { stock: 20, etf: 30 }, lastAssetType: 'stock',
  }))

  await act(async () => render('/ai-screener?asset_type=etf'))
  await settle()

  expect(fixtures.calls[0]).toBe('etf')
  expect(viewState().selectedByAsset).toEqual({ stock: '600000.SH', etf: '510300.SH' })
  expect(viewState().lastAssetType).toBe('etf')

  await act(async () => fixtures.resolveEtf?.(etfResponse))
  await settle()
  expect(host.textContent).toContain('沪深300 ETF')
  expect(host.textContent).toContain('510300.SH')
  expect(host.querySelector('[data-candidate="510300.SH"]')?.getAttribute('aria-pressed')).toBe('true')

  await changeSearch('')
  const stockTab = [...host.querySelectorAll('button')].find(button => button.textContent === '股票')!
  await act(async () => stockTab.click())
  await settle()
  expect(fixtures.calls).toContain('stock')
  expect(host.textContent).toContain('浦发银行')
  expect(host.textContent).toContain('600000.SH')
  expect(host.querySelector('[data-candidate="600000.SH"]')?.getAttribute('aria-pressed')).toBe('true')

  const etfTab = [...host.querySelectorAll('button')].find(button => button.textContent === 'ETF')!
  await act(async () => etfTab.click())
  await settle()
  expect(host.querySelector('[data-candidate="510300.SH"]')?.getAttribute('aria-pressed')).toBe('true')
  expect(viewState().visibleLimitByAsset).toEqual({ stock: 20, etf: 30 })
  await changeSearch('沪深300')

  const leave = [...host.querySelectorAll('a')].find(link => link.textContent === '离开选股')!
  await act(async () => leave.click())
  await settle()
  const back = [...host.querySelectorAll('a')].find(link => link.textContent === '返回选股')!
  await act(async () => back.click())
  await settle()

  expect(host.querySelector<HTMLButtonElement>('[aria-label="候选资产类型"] button[aria-pressed="true"]')?.textContent).toBe('ETF')
  expect(host.querySelector<HTMLInputElement>('input[aria-label="搜索候选"]')?.value).toBe('沪深300')
  expect(host.querySelector('[data-candidate="510300.SH"]')?.getAttribute('aria-pressed')).toBe('true')
  expect(fixtures.generate).not.toHaveBeenCalled()
})

it('rejects malformed persisted view state and ignores an unsupported URL asset type', async () => {
  sessionStorage.setItem('page-view:ai-screener', JSON.stringify({
    search: 'x'.repeat(121), multiOnly: 'yes',
    selectedByAsset: { stock: 17, etf: 'bad symbol' },
    visibleLimitByAsset: { stock: -1, etf: 1001 }, lastAssetType: 'crypto',
  }))

  await act(async () => render('/ai-screener?asset_type=crypto'))
  await settle()

  expect(fixtures.calls[0]).toBe('stock')
  expect(viewState()).toMatchObject({
    search: '', multiOnly: false, lastAssetType: 'stock',
    visibleLimitByAsset: { stock: 10, etf: 10 },
  })
  expect(host.querySelector<HTMLInputElement>('input[aria-label="搜索候选"]')?.value).toBe('')
  expect(fixtures.generate).not.toHaveBeenCalled()
})
