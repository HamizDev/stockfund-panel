// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { FundRankItem } from './client'
import { AiFundScreenerPage } from './AiFundScreenerPage'

const fixtures = vi.hoisted(() => ({
  screener: vi.fn(),
  profile: vi.fn(),
  aiPickStream: vi.fn(),
  analyzeStream: vi.fn(),
}))

vi.mock('@tanstack/react-query', async () => {
  const React = await import('react')
  type QueryOptions = { queryKey: unknown[]; queryFn: (context: { signal: AbortSignal }) => unknown; enabled?: boolean }
  return {
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
  fundApi: {
    screener: fixtures.screener,
    profile: fixtures.profile,
    aiPickStream: fixtures.aiPickStream,
    analyzeStream: fixtures.analyzeStream,
  },
}))
vi.mock('@/lib/api', () => ({ api: { strategyAiStatus: async () => ({ configured: false }) } }))
vi.mock('./candidateRanking', () => ({
  fundRankingBasis: () => '测试排名依据',
  mergeFundRankingCandidates: (current: FundRankItem[]) => current,
  rankFundCandidates: (items: FundRankItem[]) => items.map((item, index) => ({
    item, rank: index + 1, category: item.fund_type, rankSource: '',
  })),
}))
vi.mock('./FundCandidateCard', () => ({
  FundCandidateCard: ({ item, selected, onSelect }: {
    item: { code: string; name: string }; selected: boolean; onSelect: (code: string) => void
  }) => <button type="button" data-fund-code={item.code} aria-pressed={selected} onClick={() => onSelect(item.code)}>{item.name}</button>,
}))
vi.mock('./FundResearchPanel', () => ({ FundResearchPanel: () => null, rememberFundResearch: vi.fn() }))
vi.mock('@/components/financials/MarkdownRenderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div data-report>{content}</div>,
}))
vi.mock('@/components/PageHeader', () => ({ PageHeader: ({ title }: { title: string }) => <header>{title}</header> }))

function candidate(index: number): FundRankItem {
  const code = `5100${String(index).padStart(2, '0')}`
  return {
    code, name: `ETF 基金 ${index}`, share_class: 'A', nav: 1.2, nav_date: '2026-10-14', purchase_fee_text: '0.1%',
    fund_type: '指数型', ranking_rank: index,
    growth_1w: 0.01, growth_1m: 0.02, growth_3m: 0.03, growth_6m: 0.04, growth_1y: 0.05, growth_2y: 0.06, growth_3y: 0.07,
  }
}

const candidates = Array.from({ length: 12 }, (_, index) => candidate(index + 1))
const RESULT_STORAGE_KEY = 'stockfund.ai-fund-screener.result.v1'

function completedResult(extra: Record<string, unknown> = {}) {
  return {
    fundType: '指数型', horizon: '1y', share: 'all', candidates,
    selectedCode: candidates[0].code, report: '已有的完整 AI 报告', aiComparedCandidateCount: 12,
    retrievedAt: 1_791_970_000_000, resultHorizon: '1y', resultFundType: '指数型', resultShare: 'all',
    fundAnalyses: { [candidates[0].code]: { status: 'done', summary: '摘要', content: '完整单只报告', generatedAt: 1_791_970_000_000 } },
    ...extra,
  }
}

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  sessionStorage.clear()
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixtures.screener.mockReset().mockResolvedValue({ items: [] })
  fixtures.profile.mockReset().mockResolvedValue({ profile: null })
  fixtures.aiPickStream.mockReset()
  fixtures.analyzeStream.mockReset()
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

function render(initialPath = '/ai-fund-screener') {
  return root.render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/ai-fund-screener" element={<AiFundScreenerPage />} />
        <Route path="/away" element={<Link to="/ai-fund-screener">返回选基</Link>} />
      </Routes>
      <Link to="/away">离开选基</Link>
    </MemoryRouter>,
  )
}

it('restores candidate search, detail tab and visible limit without losing the existing completed report', async () => {
  sessionStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify(completedResult({ candidateSearch: '', detailTab: 'compare', visibleCandidateLimit: 10 })))

  await act(async () => render())
  await settle()
  expect(host.textContent).toContain('已有的完整 AI 报告')
  expect(host.querySelectorAll('[data-fund-code]')).toHaveLength(10)

  const more = [...host.querySelectorAll('button')].find(button => button.textContent?.includes('再显示'))!
  await act(async () => more.click())
  const search = host.querySelector<HTMLInputElement>('input[aria-label="搜索基金候选"]')!
  const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    valueSetter.call(search, 'ETF 基金 1')
    search.dispatchEvent(new Event('input', { bubbles: true }))
  })
  const analysisTab = [...host.querySelectorAll('[role="tab"]')].find(tab => tab.textContent === 'AI 分析')!
  await act(async () => analysisTab.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  await settle()

  const savedView = JSON.parse(sessionStorage.getItem('page-view:ai-fund') || 'null')
  expect(savedView).toEqual({ candidateSearch: 'ETF 基金 1', detailTab: 'analysis', visibleCandidateLimit: 12 })
  expect(fixtures.aiPickStream).not.toHaveBeenCalled()
  expect(fixtures.analyzeStream).not.toHaveBeenCalled()

  const leave = [...host.querySelectorAll('a')].find(link => link.textContent === '离开选基')!
  await act(async () => leave.click())
  await settle()
  const back = [...host.querySelectorAll('a')].find(link => link.textContent === '返回选基')!
  await act(async () => back.click())
  await settle()

  expect(host.querySelector<HTMLInputElement>('input[aria-label="搜索基金候选"]')?.value).toBe('ETF 基金 1')
  expect(host.querySelector('[role="tablist"] [aria-selected="true"]')?.textContent).toBe('AI 分析')
  expect(JSON.parse(sessionStorage.getItem(RESULT_STORAGE_KEY) || '{}').report).toBe('已有的完整 AI 报告')
  expect(JSON.parse(sessionStorage.getItem(RESULT_STORAGE_KEY) || '{}').fundAnalyses[candidates[0].code].content).toBe('完整单只报告')
  expect(fixtures.aiPickStream).not.toHaveBeenCalled()
  expect(fixtures.analyzeStream).not.toHaveBeenCalled()
})

it('sanitizes invalid view fields individually and preserves a valid completed report', async () => {
  sessionStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify(completedResult({
    candidateSearch: 'x'.repeat(121), detailTab: 'not-a-tab', visibleCandidateLimit: 1001,
  })))
  sessionStorage.setItem('page-view:ai-fund', JSON.stringify({ candidateSearch: 42, detailTab: 'broken', visibleCandidateLimit: -1 }))

  await act(async () => render())
  await settle()

  expect(host.querySelector<HTMLInputElement>('input[aria-label="搜索基金候选"]')?.value).toBe('')
  expect(host.querySelector('[role="tablist"] [aria-selected="true"]')?.textContent).toBe('整体对比')
  expect(host.textContent).toContain('已有的完整 AI 报告')
  expect(host.querySelectorAll('[data-fund-code]')).toHaveLength(10)
  expect(JSON.parse(sessionStorage.getItem('page-view:ai-fund') || 'null')).toEqual({
    candidateSearch: '', detailTab: 'compare', visibleCandidateLimit: 10,
  })
  const result = JSON.parse(sessionStorage.getItem(RESULT_STORAGE_KEY) || '{}')
  expect(result.report).toBe('已有的完整 AI 报告')
  expect(result.candidates).toHaveLength(12)
  expect(result.candidateSearch).toBe('')
  expect(result.detailTab).toBe('compare')
  expect(result.visibleCandidateLimit).toBe(10)
  expect(fixtures.aiPickStream).not.toHaveBeenCalled()
  expect(fixtures.analyzeStream).not.toHaveBeenCalled()
})
