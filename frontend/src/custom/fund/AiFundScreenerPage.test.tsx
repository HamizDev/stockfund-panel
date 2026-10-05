// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { AiFundScreenerPage } from './AiFundScreenerPage'
import { fundApi, type FundRankItem } from './client'

const RESULT_STORAGE_KEY = 'stockfund.ai-fund-screener.result.v1'

function fundCandidate(index: number, fundType = '股票型'): FundRankItem {
  return {
    code: String(100000 + index),
    name: `候选基金${index}`,
    fund_type: fundType as FundRankItem['fund_type'],
    share_class: 'A',
    nav: 1,
    nav_date: '2026-10-01',
    purchase_fee_text: null,
    growth_1w: null,
    growth_1m: index,
    growth_3m: index,
    growth_6m: index,
    growth_1y: index,
    growth_2y: index,
    growth_3y: null,
  }
}

function seedSavedResult(withAnalysis = false): FundRankItem[] {
  const candidates = Array.from({ length: 10 }, (_, index) => fundCandidate(index + 1))
  window.sessionStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify({
    fundType: '股票型',
    horizon: '1y',
    share: 'all',
    candidates,
    selectedCode: candidates[0].code,
    report: '保留的整体 AI 对比报告',
    aiComparedCandidateCount: 10,
    retrievedAt: 1_790_000_000_000,
    resultHorizon: '1y',
    resultFundType: '股票型',
    resultShare: 'all',
    ...(withAnalysis ? { fundAnalyses: {
      [candidates[0].code]: {
        status: 'done', summary: '保留的单只 AI 分析', content: '已有的单只分析正文', generatedAt: 1_790_000_000_000,
        navDate: '2026-10-01', holdingsReportDate: '2026-06-30',
      },
    } } : {}),
  }))
  return candidates
}

function seedLegacyAllTypeResult() {
  const candidates = Array.from({ length: 10 }, (_, index) => {
    const item = fundCandidate(index + 1)
    delete item.fund_type
    return item
  })
  window.sessionStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify({
    fundType: 'all', horizon: '1y', share: 'all', candidates,
    selectedCode: candidates[0].code, report: '旧版全部类型报告', retrievedAt: 1_790_000_000_000,
    resultHorizon: '1y', resultFundType: 'all', resultShare: 'all',
  }))
}

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

function renderPage() {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  act(() => root.render(<QueryClientProvider client={queryClient}>
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AiFundScreenerPage />
    </MemoryRouter>
  </QueryClientProvider>))
}

async function settleQueries() {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

async function searchFor(term: string) {
  const input = host.querySelector<HTMLInputElement>('[aria-label="搜索基金候选"]')
  if (!input) throw new Error('基金候选搜索框未渲染')
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
  if (!setter) throw new Error('无法更新测试搜索框')
  await act(async () => {
    setter.call(input, term)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await settleQueries()
}

async function clickButton(label: string) {
  const button = Array.from(host.querySelectorAll('button')).find(item => item.textContent?.includes(label))
  if (!button) throw new Error(`找不到按钮：${label}`)
  await act(async () => {
    button.click()
    await Promise.resolve()
  })
  await settleQueries()
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  window.sessionStorage.clear()
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn().mockReturnValue({ matches: false }) })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  vi.spyOn(api, 'strategyAiStatus').mockResolvedValue({ configured: true, has_key: true, has_model: true, provider: 'Codex' })
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient?.clear()
  vi.restoreAllMocks()
  host.remove()
  window.sessionStorage.clear()
})

describe('AI fund screener Top 10 and on-demand ranking search', () => {
  it('searches beyond the initial AI Top 10 and shows the position in the filtered category results', async () => {
    const topTen = seedSavedResult()
    const ranking = Array.from({ length: 50 }, (_, index) => fundCandidate(index + 1))
    vi.spyOn(fundApi, 'screener').mockResolvedValue({
      fund_type: '股票型', mode: 'manual', count: ranking.length, items: ranking,
    })

    renderPage()
    expect(host.querySelectorAll('article[aria-label$="候选基金"]')).toHaveLength(10)
    expect(host.textContent).toContain('保留的整体 AI 对比报告')

    await searchFor('100011')

    const eleventh = host.querySelector('article[aria-label="候选基金11候选基金"]')
    expect(eleventh).not.toBeNull()
    expect(eleventh?.textContent).toContain('股票型筛选候选 #11')
    expect(host.textContent).toContain('本次整体 AI 对比使用 10 只候选')
    expect(fundApi.screener).toHaveBeenCalledWith('股票型', expect.objectContaining({
      share: 'all', sortBy: '1y', limit: 50, signal: expect.any(AbortSignal),
    }))
    const saved = JSON.parse(window.sessionStorage.getItem(RESULT_STORAGE_KEY) ?? '{}') as {
      report?: string
      aiComparedCandidateCount?: number
      candidates?: FundRankItem[]
    }
    expect(saved.report).toBe('保留的整体 AI 对比报告')
    expect(saved.aiComparedCandidateCount).toBe(10)
    expect(saved.candidates?.some(item => item.code === '100011')).toBe(true)
    expect(topTen.every(item => saved.candidates?.some(candidate => candidate.code === item.code))).toBe(true)
  })

  it('keeps the saved candidates and AI report visible when the extra ranking request fails', async () => {
    const topTen = seedSavedResult()
    vi.spyOn(fundApi, 'screener').mockRejectedValue(new Error('公开榜单暂不可用'))

    renderPage()
    await searchFor('not-a-fund')

    expect(host.textContent).toContain('额外公开榜单暂不可用')
    expect(host.textContent).toContain('保留的整体 AI 对比报告')
    expect(host.textContent).toContain('候选基金1')
    expect(JSON.parse(window.sessionStorage.getItem(RESULT_STORAGE_KEY) ?? '{}')).toMatchObject({
      report: '保留的整体 AI 对比报告',
      aiComparedCandidateCount: 10,
      candidates: topTen,
    })

    await searchFor('')
    expect(host.querySelector('article[aria-label="候选基金1候选基金"]')).not.toBeNull()
    expect(host.textContent).toContain('股票型类别候选 #1')
  })

  it('uses a fresh ranking query after a new AI run while keeping the new AI comparison at ten', async () => {
    seedSavedResult()
    const ranking = Array.from({ length: 50 }, (_, index) => fundCandidate(index + 1))
    const latestAiCandidates = Array.from({ length: 10 }, (_, index) => fundCandidate(index + 201))
    vi.spyOn(fundApi, 'screener').mockResolvedValue({
      fund_type: '股票型', mode: 'manual', count: ranking.length, items: ranking,
    })
    vi.spyOn(fundApi, 'aiPickStream').mockImplementation(async function* () {
      yield { type: 'meta', candidates: latestAiCandidates, retrieved_at_ms: 1_790_000_000_000 }
      yield { type: 'delta', content: '新一轮10只报告' }
      yield { type: 'done' }
    })

    renderPage()
    await searchFor('100011')
    expect(host.textContent).toContain('股票型筛选候选 #11')
    await searchFor('')

    await clickButton('生成研究候选')
    expect(host.textContent).toContain('新一轮10只报告')
    expect(host.textContent).toContain('本次整体 AI 对比使用 10 只候选')
    expect(host.querySelectorAll('article[aria-label$="候选基金"]')).toHaveLength(10)
    expect(host.textContent).toContain('加载更多公开榜单候选')

    await clickButton('加载更多公开榜单候选')
    await searchFor('100011')

    expect(fundApi.screener).toHaveBeenCalledTimes(2)
    expect(host.textContent).toContain('股票型筛选候选 #11')
    expect(host.textContent).toContain('新一轮10只报告')
    expect(host.textContent).toContain('本次整体 AI 对比使用 10 只候选')
  })

  it('restores the last successful result and keeps per-fund AI analysis when a new run fails', async () => {
    seedSavedResult(true)
    vi.spyOn(fundApi, 'aiPickStream').mockImplementation(async function* () {
      yield { type: 'meta', candidates: [fundCandidate(201)], retrieved_at_ms: 1_800_000_000_000 }
      yield { type: 'error', message: '本轮模型请求失败' }
    })

    renderPage()
    await clickButton('生成研究候选')

    expect(host.textContent).toContain('本轮模型请求失败')
    expect(host.textContent).toContain('上次成功结果仍显示：股票型 · 近 1 年 · all 份额')
    expect(host.textContent).toContain('保留的整体 AI 对比报告')
    expect(host.textContent).toContain('保留的单只 AI 分析')
    expect(host.querySelector('article[aria-label="候选基金1候选基金"]')).not.toBeNull()
    expect(host.querySelector('article[aria-label="候选基金201候选基金"]')).toBeNull()
    expect(JSON.parse(window.sessionStorage.getItem(RESULT_STORAGE_KEY) ?? '{}')).toMatchObject({
      report: '保留的整体 AI 对比报告',
      aiComparedCandidateCount: 10,
      fundAnalyses: {
        '100001': { summary: '保留的单只 AI 分析', status: 'done' },
      },
    })
  })

  it('marks an in-progress single-fund analysis cancelled when a new candidate run aborts it', async () => {
    const candidates = seedSavedResult()
    let analysisSignal: AbortSignal | undefined
    vi.spyOn(fundApi, 'analyzeStream').mockImplementation(async function* (_thscode, _focus, signal) {
      analysisSignal = signal
      yield { type: 'meta', summary: '单只分析资料摘要' }
      yield { type: 'delta', content: '已接收的部分分析片段' }
      await new Promise<void>((resolve) => {
        if (signal?.aborted) resolve()
        else signal?.addEventListener('abort', () => resolve(), { once: true })
      })
      if (!signal?.aborted) yield { type: 'done' }
    })
    vi.spyOn(fundApi, 'aiPickStream').mockImplementation(async function* () {
      yield { type: 'meta', candidates, retrieved_at_ms: 1_790_000_000_000 }
      yield { type: 'delta', content: '新一轮整体报告' }
      yield { type: 'done' }
    })

    renderPage()
    await clickButton('AI分析')
    expect(host.textContent).toContain('已接收的部分分析片段')

    await clickButton('生成研究候选')

    const firstCard = host.querySelector('article[aria-label="候选基金1候选基金"]')
    expect(analysisSignal?.aborted).toBe(true)
    expect(firstCard?.textContent).toContain('AI 已停止（当前未分析）')
    expect(firstCard?.textContent).toContain('已接收的部分分析片段')
    expect(firstCard?.textContent).toContain('AI分析')
  })

  it('labels unclassified candidates from an old all-type cache without assigning them a category', () => {
    seedLegacyAllTypeResult()
    renderPage()

    const firstCard = host.querySelector('article[aria-label="候选基金1候选基金"]')
    expect(firstCard?.textContent).toContain('未分类候选 #1')
    expect(firstCard?.textContent).not.toContain('股票型类别候选')
    expect(firstCard?.textContent).not.toContain('混合型类别候选')
  })
})
