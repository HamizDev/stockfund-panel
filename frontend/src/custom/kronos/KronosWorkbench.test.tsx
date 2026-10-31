// @vitest-environment jsdom
import { act, createElement, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { KronosCandidatePanel, KronosComparisonPanel, KronosSummaryPanel, kronosTimingLabel } from './KronosWorkbench'

const apiMocks = vi.hoisted(() => ({
  kronosComparisons: vi.fn(),
  kronosComparison: vi.fn(),
  kronosCreateComparison: vi.fn(),
  kronosCancelComparison: vi.fn(),
  kronosCreateCandidate: vi.fn(),
  kronosComparisonSummary: vi.fn(),
  kronosCandidates: vi.fn(),
  kronosRetireCandidate: vi.fn(),
  kronosEvaluation: vi.fn(),
  kronosJob: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ api: apiMocks }))
vi.mock('@/components/financials/StockFinancialSearch', () => ({
  StockFinancialSearch: () => <div data-testid="stock-search" />,
}))
vi.mock('./KronosChart', () => ({ KronosChart: () => <div data-testid="kronos-chart" /> }))
vi.mock('./KronosEvaluation', () => ({ KronosEvaluation: () => <div data-testid="kronos-evaluation" /> }))

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const selection = { symbol: '600000.SH', name: '浦发银行', assetType: 'stock' as const }
const request = { symbol: selection.symbol, name: selection.name, period: 'daily', models: ['small', 'base'], lookbacks: [60, 120], horizon: 5, samples: 1 }
const trialSummary = {
  id: 'trial-1', model: 'small', lookback: 60, state: 'complete', job_id: 'job-1',
  error: null, completed_at: '2026-10-27T02:00:00Z', timely: false,
}
const comparisonSummary = {
  id: 'comparison-1', state: 'partial', request,
  created_at: '2026-10-27T01:00:00Z', updated_at: '2026-10-27T02:00:00Z',
  error: null, snapshot: null, trials: [trialSummary],
}
const comparisonDetail = {
  ...comparisonSummary,
  trials: [
    { ...trialSummary, job: null, evaluation: null },
    { ...trialSummary, id: 'trial-2', model: 'base', lookback: 120, timely: true, job_id: null, job: null, evaluation: null },
  ],
}

function mount(element: ReactNode): HTMLElement {
  const host = document.createElement('div')
  document.body.append(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const root: Root = createRoot(host)
  ;(host as HTMLElement & { __root?: Root; __client?: QueryClient }).__root = root
  ;(host as HTMLElement & { __root?: Root; __client?: QueryClient }).__client = client
  act(() => root.render(createElement(QueryClientProvider, { client }, element)))
  return host
}

async function flushQueries() {
  await act(async () => {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, 10))
      await Promise.resolve()
    }
  })
}

let hosts: HTMLElement[] = []
beforeEach(() => {
  vi.clearAllMocks()
  hosts = []
  window.sessionStorage.clear()
})
afterEach(() => {
  for (const host of hosts) {
    const typed = host as HTMLElement & { __root?: Root; __client?: QueryClient }
    act(() => typed.__root?.unmount())
    typed.__client?.clear()
    host.remove()
  }
  window.sessionStorage.clear()
})

describe('Kronos comparison workbench', () => {
  it('loads one batch detail for the selected scope and labels delayed trials as non-real-time', async () => {
    apiMocks.kronosComparisons.mockResolvedValue({ items: [comparisonSummary], unavailable: 1 })
    apiMocks.kronosComparison.mockResolvedValue(comparisonDetail)
    const host = mount(createElement(KronosComparisonPanel, {
      selection,
      period: 'daily',
      onSelect: vi.fn(),
      onPeriodChange: vi.fn(),
    }))
    hosts.push(host)
    await flushQueries()
    expect(apiMocks.kronosComparison).toHaveBeenCalledTimes(1)
    await act(async () => {
      await (apiMocks.kronosComparison.mock.results[0]?.value as Promise<unknown>)
      await Promise.resolve()
    })
    await flushQueries()

    expect(apiMocks.kronosComparisons).toHaveBeenCalledTimes(1)
    expect(apiMocks.kronosComparisons).toHaveBeenCalledWith(selection.symbol, 'daily', expect.anything())
    expect(apiMocks.kronosComparison).toHaveBeenCalledTimes(1)
    expect(apiMocks.kronosComparison).toHaveBeenCalledWith('comparison-1', expect.anything())
    expect(apiMocks.kronosJob).not.toHaveBeenCalled()
    expect(apiMocks.kronosEvaluation).not.toHaveBeenCalled()
    expect(host.textContent).toContain('选择批次')
    expect(host.textContent).toContain('1 个批次记录不可读取')
    expect(kronosTimingLabel(false)).toBe('迟到结果 · 非实时预测')
    expect(kronosTimingLabel(null)).toBe('时效未判定')

    const curveButton = host.querySelector<HTMLButtonElement>('button[aria-label="查看Base窗口120根的曲线"]')
    expect(curveButton).not.toBeNull()
    await act(async () => curveButton?.click())
    expect(curveButton?.getAttribute('aria-pressed')).toBe('true')
    expect(host.textContent).toContain('当前配置：Base · 120 根')
  })

  it('refreshes the selected batch detail together with the comparison list', async () => {
    apiMocks.kronosComparisons.mockResolvedValue({ items: [comparisonSummary], unavailable: 0 })
    apiMocks.kronosComparison.mockResolvedValue(comparisonDetail)
    const host = mount(createElement(KronosComparisonPanel, {
      selection,
      period: 'daily',
      onSelect: vi.fn(),
      onPeriodChange: vi.fn(),
    }))
    hosts.push(host)
    await flushQueries()
    await act(async () => {
      await Promise.all([
        apiMocks.kronosComparisons.mock.results[0]?.value as Promise<unknown>,
        apiMocks.kronosComparison.mock.results[0]?.value as Promise<unknown>,
      ])
    })
    await flushQueries()
    expect(apiMocks.kronosComparisons).toHaveBeenCalledTimes(1)
    expect(apiMocks.kronosComparison).toHaveBeenCalledTimes(1)

    const refreshButton = Array.from(host.querySelectorAll('button')).find(button => button.textContent?.includes('刷新记录'))
    expect(refreshButton?.disabled).toBe(false)
    await act(async () => refreshButton?.click())
    await flushQueries()

    expect(apiMocks.kronosComparisons).toHaveBeenCalledTimes(2)
    expect(apiMocks.kronosComparison).toHaveBeenCalledTimes(2)
  })

  it('disables new comparison creation while this symbol and period have an active batch', async () => {
    apiMocks.kronosComparisons.mockResolvedValue({
      items: [{ ...comparisonSummary, state: 'running' }],
      unavailable: 0,
    })
    apiMocks.kronosComparison.mockResolvedValue({ ...comparisonDetail, state: 'running' })
    const host = mount(createElement(KronosComparisonPanel, {
      selection,
      period: 'daily',
      onSelect: vi.fn(),
      onPeriodChange: vi.fn(),
    }))
    hosts.push(host)
    await flushQueries()
    await act(async () => {
      await (apiMocks.kronosComparison.mock.results[0]?.value as Promise<unknown>)
      await Promise.resolve()
    })
    await flushQueries()

    const createButton = Array.from(host.querySelectorAll('button')).find(button => button.textContent?.includes('开始配置比较'))
    expect(createButton?.disabled).toBe(true)
    expect(host.textContent).toContain('已有活动比较')
  })

  it('uses the selected detail state instead of a stale active list summary', async () => {
    apiMocks.kronosComparisons.mockResolvedValue({
      items: [{ ...comparisonSummary, state: 'running' }],
      unavailable: 0,
    })
    apiMocks.kronosComparison.mockResolvedValue({ ...comparisonDetail, state: 'complete' })
    const host = mount(createElement(KronosComparisonPanel, {
      selection,
      period: 'daily',
      onSelect: vi.fn(),
      onPeriodChange: vi.fn(),
    }))
    hosts.push(host)
    await flushQueries()
    expect(apiMocks.kronosComparison).toHaveBeenCalledTimes(1)
    await act(async () => {
      await (apiMocks.kronosComparison.mock.results[0]?.value as Promise<unknown>)
      await Promise.resolve()
    })
    await flushQueries()

    const createButton = Array.from(host.querySelectorAll('button')).find(button => button.textContent?.includes('开始配置比较'))
    expect(createButton?.disabled).toBe(false)
    expect(host.textContent).not.toContain('已有活动比较')
  })

  it('disables candidate registration when a completed trial carries an error', async () => {
    apiMocks.kronosComparisons.mockResolvedValue({ items: [comparisonSummary], unavailable: 0 })
    apiMocks.kronosComparison.mockResolvedValue({
      ...comparisonDetail,
      trials: [{
        ...comparisonDetail.trials[0],
        error: '输出核验不通过',
        job: { id: 'job-1', result: { input_as_of: '2026-10-27T02:00:00Z', period: 'daily', price_basis: 'close' } },
        evaluation: { state: 'complete' },
      }, comparisonDetail.trials[1]],
    })
    const host = mount(createElement(KronosComparisonPanel, {
      selection,
      period: 'daily',
      onSelect: vi.fn(),
      onPeriodChange: vi.fn(),
    }))
    hosts.push(host)
    await flushQueries()
    expect(apiMocks.kronosComparison).toHaveBeenCalledTimes(1)
    await act(async () => {
      await (apiMocks.kronosComparison.mock.results[0]?.value as Promise<unknown>)
      await Promise.resolve()
    })
    await flushQueries()

    const saveButton = Array.from(host.querySelectorAll('button')).find(button => button.textContent?.includes('登记候选配置'))
    expect(saveButton?.disabled).toBe(true)
    expect(host.textContent).toContain('配置错误：输出核验不通过')
    expect(host.querySelector('[data-testid="kronos-chart"]')).toBeNull()
    expect(host.querySelector('[data-testid="kronos-evaluation"]')).toBeNull()
  })

  it('shows coverage while retaining completed-pair metrics and hides metrics with no completed pairs', async () => {
    apiMocks.kronosComparisonSummary.mockResolvedValue({
      groups: [
        {
          id: 'partial-1', symbol: selection.symbol, model: 'small', lookback: 60, period: 'daily', horizon: 5, samples: 1,
          origins: 2, complete_origins: 1, timely_origins: 0, matched: 2, expected: 10, coverage: 0.2,
          mae: 5.5, mape: 0.3, direction_accuracy: 0.6, baseline_mae: 10, baseline_skill: 0.45,
          latency_seconds: 1.25, peak_mb: 256, eligible_for_ranking: false, reason: '等待后一批行情',
        },
        {
          id: 'waiting-1', symbol: selection.symbol, model: 'base', lookback: 120, period: 'daily', horizon: 5, samples: 1,
          origins: 1, complete_origins: 0, timely_origins: 0, matched: 0, expected: 10, coverage: 0,
          mae: 99, mape: 0.99, direction_accuracy: 0.99, baseline_mae: 98, baseline_skill: 0.5,
          latency_seconds: 8, peak_mb: 900, eligible_for_ranking: false, reason: '尚无完整成对起点',
        },
      ],
      batches: 2,
      legacy_jobs: 3,
      training_enabled: false,
      automatic_promotion: false,
    })
    const host = mount(createElement(KronosSummaryPanel, {
      selection,
      period: 'daily',
      onSelect: vi.fn(),
      onPeriodChange: vi.fn(),
    }))
    hosts.push(host)
    await flushQueries()

    expect(apiMocks.kronosComparisonSummary).toHaveBeenCalledTimes(1)
    expect(host.textContent).toContain('指标仅统计已完成成对起点')
    expect(host.textContent).toContain('部分 / 等待')
    expect(host.textContent).toContain('20.00%')
    expect(host.textContent).toContain('不评冠军')
    const rows = host.querySelectorAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('5.500000')
    expect(rows[0].textContent).toContain('30.00%')
    expect(rows[0].textContent).toContain('1.25 秒 / 256.0 MB')
    expect(Array.from(rows[1].querySelectorAll('td')).slice(4).map(cell => cell.textContent?.trim()))
      .toEqual(['—', '—', '—', '—', '—', '—'])
  })

  it('loads candidate and baseline lists in one request and avoids implying fine-tuned weights', async () => {
    const hash = 'a'.repeat(64)
    apiMocks.kronosCandidates.mockResolvedValue({
      baselines: [{ id: 'baseline-small', label: 'Small 基线', sha256: hash, installed: true, verification: 'configured', kind: 'baseline' }],
      items: [{
        id: 'candidate-1', label: '短窗配置', model: 'small', lookback: 120, horizon: 10, samples: 3,
        period: 'daily', model_sha256: hash, tokenizer_sha256: hash, seed: 42,
        source_comparison_id: 'comparison-1', source_trial_id: 'trial-1',
        created_at: '2026-10-27T02:00:00Z', state: 'registered',
      }],
      training_enabled: false,
      automatic_promotion: false,
    })
    const host = mount(createElement(KronosCandidatePanel))
    hosts.push(host)
    await flushQueries()

    expect(apiMocks.kronosCandidates).toHaveBeenCalledTimes(1)
    expect(host.textContent).toContain('基线模型＋候选配置')
    expect(host.textContent).toContain('不包含微调权重')
    expect(host.textContent).toContain('seed 42')
    expect(apiMocks.kronosJob).not.toHaveBeenCalled()
    expect(apiMocks.kronosEvaluation).not.toHaveBeenCalled()
  })
})
