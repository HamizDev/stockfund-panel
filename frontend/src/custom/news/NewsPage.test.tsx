// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import type { NewsFeedResponse } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { NewsPage } from './NewsPage'
import { beijingNewsDay, readNewsFeedSessionCache, writeNewsFeedSessionCache } from './newsFeedSessionCache'

const state = vi.hoisted(() => ({
  data: {} as NewsFeedResponse,
  jevosStatus: null as null | Record<string, unknown>,
  refreshes: [] as boolean[], analyses: [] as string[], directionStarts: [] as string[], directionPolls: [] as string[],
  jevosStatusCalls: 0,
  directionStartResult: {} as { id: string; status: string; total: number; completed: number; error: string | null; model: string; reasoning_effort: string; cache_hit: boolean },
  directionJobResult: {} as { id: string; status: string; total: number; completed: number; error: string | null; model: string; reasoning_effort: string; cache_hit: boolean },
  busy: false, feedError: '', directionPollError: 0,
}))
vi.mock('@/components/StockPreviewDialog', () => ({ StockPreviewDialog: ({ symbol }: { symbol: string }) => <div>{symbol}预览</div> }))
vi.mock('@/lib/api', () => {
  class MockApiError extends Error {
    constructor(message: string, readonly status: number) { super(message) }
  }
  return { ApiError: MockApiError, api: {
  jevosStatus: async () => { state.jevosStatusCalls += 1; return state.jevosStatus },
  newsFeed: async (refresh = false) => {
    state.refreshes.push(refresh)
    if (!refresh && state.feedError) throw new Error(state.feedError)
    return state.data
  },
  newsDirections: async (effort: string) => {
    state.directionStarts.push(effort)
    const result = { ...state.directionStartResult, reasoning_effort: effort }
    state.directionJobResult = { ...result }
    return result
  },
  newsDirectionJob: async (id: string) => {
    state.directionPolls.push(id)
    if (state.directionPollError) throw new MockApiError('direction job unavailable', state.directionPollError)
    return state.directionJobResult
  },
  newsAnalyze: async (id: string) => {
    state.analyses.push(id)
    return { id: 'a'.repeat(32), article_id: id, status: state.busy ? 'busy' : 'unconfigured', error: state.busy ? '已有 AI 解读正在运行' : '请先配置 AI', content: null, model: 'gpt-6.1-sol', reasoning_effort: 'xhigh', generated_at: null }
  },
  newsAnalysis: async () => ({ status: 'failed', error: 'not found' }),
} }
})

let host: HTMLDivElement
let root: Root
let client: QueryClient
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  sessionStorage.clear()
  state.refreshes = []; state.analyses = []; state.directionStarts = []; state.directionPolls = []; state.jevosStatusCalls = 0; state.busy = false; state.feedError = ''; state.directionPollError = 0
  state.jevosStatus = { mode: 'shadow', status: 'offline', model: null, fingerprint: null, last_check_ms: null, queued: 0, completed: 0, news: {}, strategies: {}, strategy_history: [] }
  state.directionStartResult = { id: 'direction-job-1', status: 'running', total: 240, completed: 0, error: null, model: 'gpt-6-luna', reasoning_effort: 'high', cache_hit: false }
  state.directionJobResult = { ...state.directionStartResult }
  state.data = {
    today: beijingNewsDay(), fetched_at: `${beijingNewsDay()}T10:00:00+08:00`, association_status: 'ok', related_symbols: ['601398.SH'],
    sources: [{ source: 'cls', label: '财联社', status: 'ok', fetched_at: `${beijingNewsDay()}T10:00:00+08:00`, count: 1, reason: null },
      { source: 'eastmoney', label: '东财7×24', status: 'unavailable', fetched_at: null, count: 0, reason: '接口不可用' }],
    summary: { today_count: 2, total_count: 2, themes: [{ label: '资本运作', count: 1 }] },
    items: [
      { id: '1'.repeat(24), title: '工商银行回购公告', summary: '公司披露回购计划', published_at: `${beijingNewsDay()}T10:00:00+08:00`,
        origins: [{ source: 'cls', label: '财联社', url: 'https://www.cls.cn/detail/123', published_at: `${beijingNewsDay()}T10:00:00+08:00` }],
        classifications: { serenity: { category: 'capital', label: '资本运作', score: 18, matched: ['回购'] }, wojianshan: { category: 'capital', label: '资本运作', score: 23, matched: ['回购'] } },
        sentiment: { direction: 'positive', matched: ['回购'] },
        associations: [{ symbol: '601398.SH', name: '工商银行', asset_type: 'stock', direct: true, basis: '正文明确出现完整名称 工商银行' }] },
      { id: '2'.repeat(24), title: '某公司遭处罚', summary: '监管发布调查结果', published_at: `${beijingNewsDay()}T09:00:00+08:00`,
        origins: [{ source: 'eastmoney', label: '东财7×24', url: null, published_at: `${beijingNewsDay()}T09:00:00+08:00` }],
        classifications: { serenity: { category: 'risk', label: '风险·负面', score: 38, matched: ['处罚'] }, wojianshan: { category: 'risk', label: '排雷·规避', score: 43, matched: ['处罚'] } },
        sentiment: { direction: 'negative', matched: ['处罚'] }, associations: [] },
    ],
  }
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove() })
async function render(path = '/news') {
  await act(async () => root.render(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={client}><NewsPage /></QueryClientProvider></MemoryRouter>))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)) })
}
function button(label: string) {
  const el = [...host.querySelectorAll('button')].find(item => item.textContent?.trim() === label || item.getAttribute('aria-label') === label)
  if (!el) throw new Error(`Missing ${label}`)
  return el
}
function directionBadge(row = 0) {
  const badge = host.querySelectorAll('article')[row]?.querySelector('[aria-label="快讯方向"]')
  if (!badge) throw new Error(`Missing news direction badge for row ${row}`)
  return badge
}
async function click(label: string) { await act(async () => button(label).click()); await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) }) }
async function chooseDirectionEffort(effort: 'high' | 'max') {
  const select = host.querySelector<HTMLSelectElement>('select[aria-label="AI方向推理档位"]')
  if (!select) throw new Error('Missing AI direction effort selector')
  await act(async () => {
    select.value = effort
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

it('renders source failure independently, original links and explicit model action', async () => {
  await render()
  expect(host.querySelectorAll('article')).toHaveLength(2)
  expect(host.textContent).toContain('东财7×24 不可用')
  expect(host.textContent).toContain('拉取 尚未取得')
  expect(host.querySelector('a[href="https://www.cls.cn/detail/123"]')?.getAttribute('rel')).toContain('noreferrer')
  expect(state.analyses).toEqual([])
  expect(state.directionStarts).toEqual([])
  expect(directionBadge(0).textContent).toBe('待判断')
  expect(directionBadge(0).getAttribute('title')).toContain('正向词：回购')
  expect(directionBadge(1).textContent).toBe('待判断')
  expect(directionBadge(1).getAttribute('title')).toContain('负向词：处罚')
  expect(host.textContent).not.toContain('中性')
  expect(state.jevosStatusCalls).toBe(1)
  expect(sessionStorage.getItem('stockfund.news-direction-job.v1')).toBeNull()
})

it('adds a Jevos opinion only to its matching news row and keeps the existing feed visible', async () => {
  const id = '1'.repeat(24)
  state.jevosStatus = {
    mode: 'shadow', status: 'ready', model: 'jevos-v5', fingerprint: 'b'.repeat(64), last_check_ms: 1_800_000_000_000,
    queued: 0, completed: 1, strategies: {}, strategy_history: [],
    news: { [id]: {
      kind: 'news', id, input_key: 'a'.repeat(64), model: 'jevos-v5', fingerprint: 'b'.repeat(64),
      generated_at_ms: 1_800_000_000_000, source_at_ms: null, label: 'positive_clue', original_signal: null,
      probabilities: { positive: 0.9, risk: 0.05, material: 0.8 }, related_names: ['工商银行'],
    } },
  }
  await render()

  const rows = host.querySelectorAll('article')
  expect(rows).toHaveLength(2)
  expect(rows[0].textContent).toContain('Jevos 初筛')
  expect(directionBadge(0).textContent).toBe('偏利好 · Jevos 初筛')
  expect(rows[0].textContent).toContain('关注 / 登记持仓')
  expect(directionBadge(1).textContent).toBe('待判断')
  expect(rows[1].textContent).not.toContain('Jevos 初筛')
  expect(host.textContent).toContain('公司遭处罚')
})

it('uses Jevos mixed and unclear news labels in the card header', async () => {
  const firstId = '1'.repeat(24)
  const secondId = '2'.repeat(24)
  const review = (id: string, label: 'mixed' | 'unclear') => ({
    kind: 'news' as const, id, input_key: 'a'.repeat(64), model: 'jevos-v5', fingerprint: 'b'.repeat(64),
    generated_at_ms: 1_800_000_000_000, source_at_ms: null, label, original_signal: null,
    probabilities: { positive: 0.45, risk: 0.4, material: 0.8 }, related_names: [],
  })
  state.jevosStatus = {
    mode: 'shadow', status: 'ready', model: 'jevos-v5', fingerprint: 'b'.repeat(64), last_check_ms: 1_800_000_000_000,
    queued: 0, completed: 2, strategies: {}, strategy_history: [],
    news: { [firstId]: review(firstId, 'mixed'), [secondId]: review(secondId, 'unclear') },
  }
  await render()

  expect(directionBadge(0).textContent).toBe('正负混合 · Jevos 初筛')
  expect(directionBadge(0).className).toContain('amber')
  expect(directionBadge(0).getAttribute('title')).toContain('展开查看概率与详情')
  expect(directionBadge(0).getAttribute('title')).not.toContain('positive')
  expect(directionBadge(1).textContent).toBe('信息不足 · Jevos 初筛')
  expect(directionBadge(1).getAttribute('title')).toContain('信息不足')
  expect(directionBadge(1).textContent).not.toContain('中性')
})

it('shows tentative Jevos direction with its source excerpt and maps model neutral explicitly', async () => {
  const firstId = '1'.repeat(24)
  const secondId = '2'.repeat(24)
  const review = (id: string, fields: Record<string, unknown>) => ({
    kind: 'news' as const, id, input_key: 'a'.repeat(64), model: 'jevos-v5', fingerprint: 'b'.repeat(64),
    generated_at_ms: 1_800_000_000_000, source_at_ms: null, label: 'positive_clue', original_signal: null,
    probabilities: { positive: 0.45, negative: 0.1, neutral: 0.05, mixed: 0.1, uncertain: 0.3 }, related_names: [],
    ...fields,
  })
  state.jevosStatus = {
    mode: 'shadow', status: 'ready', model: 'jevos-v5', fingerprint: 'b'.repeat(64), last_check_ms: 1_800_000_000_000,
    queued: 0, completed: 2, strategies: {}, strategy_history: [],
    news: {
      [firstId]: review(firstId, { label: 'risk_clue', tentative: true, reason: '模型选取的相关原文片段：公司受到行政处罚', evidence: ['公司受到行政处罚'] }),
      [secondId]: review(secondId, { label: 'neutral', reason: '日常事项未见明确经营影响。' }),
    },
  }
  await render()

  expect(directionBadge(0).textContent).toBe('偏利空（较弱） · Jevos 初筛')
  const firstRow = host.querySelectorAll('article')[0]
  expect(firstRow?.querySelector('[aria-label="Jevos方向摘要"]')?.textContent)
    .toContain('模型选取的相关原文片段：公司受到行政处罚')
  expect(firstRow?.querySelector('details')?.textContent).toContain('公司受到行政处罚')
  expect(directionBadge(1).textContent).toBe('中性 · Jevos 初筛')
})

it('keeps Luna uncertain ahead of a conflicting Jevos positive clue', async () => {
  const id = '1'.repeat(24)
  state.jevosStatus = {
    mode: 'shadow', status: 'ready', model: 'jevos-v5', fingerprint: 'b'.repeat(64), last_check_ms: 1_800_000_000_000,
    queued: 0, completed: 1, strategies: {}, strategy_history: [],
    news: { [id]: {
      kind: 'news', id, input_key: 'a'.repeat(64), model: 'jevos-v5', fingerprint: 'b'.repeat(64),
      generated_at_ms: 1_800_000_000_000, source_at_ms: null, label: 'positive_clue', original_signal: null,
      probabilities: { positive: 0.9, risk: 0.05, material: 0.8 }, related_names: [],
    } },
  }
  state.data.items[0].ai_direction = {
    status: 'complete', direction: 'uncertain', reason: '暂无充分证据判断方向。', evidence: [],
    scope: 'unclear', model: 'gpt-6-luna', reasoning_effort: 'high', generated_at: `${beijingNewsDay()}T10:10:00+08:00`,
  }
  await render()

  expect(directionBadge(0).textContent).toBe('信息不足 · Luna 判断')
  expect(directionBadge(0).getAttribute('title')).toContain('暂无充分证据判断方向。')
  expect(directionBadge(0).textContent).not.toContain('偏利好')
})

it('starts the selected Luna effort only after a button click and stores only job coordinates', async () => {
  await render()
  expect(button('AI 判断当日方向').disabled).toBe(false)
  expect(state.directionStarts).toEqual([])
  expect(host.textContent).toContain('当前快照最新 240 条当日快讯')

  await chooseDirectionEffort('max')
  expect(state.directionStarts).toEqual([])
  await click('AI 判断当日方向')

  expect(state.directionStarts).toEqual(['max'])
  expect(host.textContent).toContain('正在后台判断')
  expect(host.textContent).toContain('gpt-6-luna · max')
  expect(JSON.parse(sessionStorage.getItem('stockfund.news-direction-job.v1') || '{}')).toEqual({ id: 'direction-job-1', effort: 'max' })
})

it('resumes a running direction job after reload without starting another job', async () => {
  await render()
  await click('AI 判断当日方向')
  expect(state.directionStarts).toEqual(['high'])

  await act(async () => root.render(null))
  client.clear()
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  state.directionPolls = []
  await render()

  expect(state.directionStarts).toEqual(['high'])
  expect(state.directionPolls).toContain('direction-job-1')
  expect(JSON.parse(sessionStorage.getItem('stockfund.news-direction-job.v1') || '{}')).toEqual({ id: 'direction-job-1', effort: 'high' })
})

it('clears a persisted direction job after a 404 and allows a fresh manual run', async () => {
  state.directionPollError = 404
  sessionStorage.setItem('stockfund.news-direction-job.v1', JSON.stringify({ id: 'direction-job-1', effort: 'high' }))
  await render()

  expect(state.directionPolls).toContain('direction-job-1')
  expect(sessionStorage.getItem('stockfund.news-direction-job.v1')).toBeNull()
  expect(host.textContent).toContain('后台判断任务已不存在')
  expect(button('AI 判断当日方向').disabled).toBe(false)

  await click('AI 判断当日方向')
  expect(state.directionStarts).toEqual(['high'])
})

it('keeps polling and blocks duplicate starts after a temporary direction-job network error', async () => {
  state.directionPollError = 503
  sessionStorage.setItem('stockfund.news-direction-job.v1', JSON.stringify({ id: 'direction-job-1', effort: 'high' }))
  await render()

  expect(state.directionPolls).toContain('direction-job-1')
  expect(sessionStorage.getItem('stockfund.news-direction-job.v1')).not.toBeNull()
  expect(host.textContent).toContain('将继续核对且不会重复提交')
  expect(host.querySelector<HTMLButtonElement>('section[aria-label="AI 当日方向判断"] button')?.disabled).toBe(true)
  const jobQuery = client.getQueryCache().find({ queryKey: QK.newsDirectionJob('direction-job-1') })
  const queryOptions = jobQuery?.options as unknown as { refetchInterval?: unknown } | undefined
  const interval = queryOptions?.refetchInterval as ((query: unknown) => number | false) | undefined
  expect(interval?.(jobQuery)).toBe(2_000)
})

it('shows public AI direction in the header while keeping the fallback word-list clue in its tooltip', async () => {
  state.data.items[0].ai_direction = {
    status: 'complete', direction: 'positive', reason: '回购公告提供公司层面的正向证据。', evidence: ['公告披露回购计划'],
    scope: 'company', model: 'gpt-6-luna', reasoning_effort: 'high', generated_at: `${beijingNewsDay()}T10:10:00+08:00`,
  }
  await render()

  expect(directionBadge(0).textContent).toBe('偏利好 · Luna 判断')
  const summary = host.querySelectorAll('article')[0]?.querySelector('[aria-label="AI方向摘要"]')
  expect(summary?.textContent).toContain('公司 · 回购公告提供公司层面的正向证据。')
  expect(host.querySelectorAll('article')[0]?.querySelector('details')?.hasAttribute('open')).toBe(false)
  expect(host.textContent).toContain('回购公告提供公司层面的正向证据。')
  expect(host.textContent).toContain('gpt-6-luna · high')
  expect(directionBadge(1).textContent).toBe('待判断')
  expect(directionBadge(1).getAttribute('title')).toContain('负向词：处罚')
})

it('labels an in-progress source check as cached', async () => {
  state.data.refreshing = false
  state.data.sources[1] = { ...state.data.sources[1], status: 'stale', reason: '正在核对来源，当前为上次缓存' }
  await render()
  expect(host.textContent).toContain('东财7×24 缓存，后台核对')
  expect(host.textContent).not.toContain('东财7×24 连接失败，显示旧缓存')
})

it('labels an active refresh as cached when a stale source has no failure reason yet', async () => {
  state.data.refreshing = true
  state.data.sources[1] = { ...state.data.sources[1], status: 'stale', reason: null }
  await render()
  expect(host.textContent).toContain('东财7×24 缓存，后台核对')
})

it('keeps the failure label for a stale source after a real connection error', async () => {
  state.data.refreshing = true
  state.data.sources[1] = { ...state.data.sources[1], status: 'stale', reason: '连接超时' }
  await render()
  expect(host.textContent).toContain('东财7×24 连接失败，显示旧缓存')
  expect(host.textContent).not.toContain('东财7×24 缓存，后台核对')
})

it('refreshes the feed after a completed direction job and clears its session marker', async () => {
  state.directionStartResult = { ...state.directionStartResult, status: 'complete', total: 2, completed: 2, cache_hit: true }
  state.data.items[0].ai_direction = {
    status: 'complete', direction: 'positive', reason: '公告披露回购。', evidence: ['回购计划'],
    scope: 'company', model: 'gpt-6-luna', reasoning_effort: 'high', generated_at: `${beijingNewsDay()}T10:10:00+08:00`,
  }
  await render()
  await click('AI 判断当日方向')

  expect(state.directionStarts).toEqual(['high'])
  expect(state.refreshes).toEqual([false, false])
  expect(sessionStorage.getItem('stockfund.news-direction-job.v1')).toBeNull()
  expect(directionBadge(0).textContent).toBe('偏利好 · Luna 判断')
})

it('returns to same-day cached query data without requesting the feed again', async () => {
  await render()
  expect(state.refreshes).toEqual([false])
  await act(async () => root.render(null))
  await render()

  expect(state.refreshes).toEqual([false])
  const options = client.getQueryCache().find({ queryKey: QK.newsFeed })?.options as unknown as {
    refetchOnMount?: unknown; refetchInterval?: unknown; refetchIntervalInBackground?: unknown
  } | undefined
  expect(options).toMatchObject({ refetchIntervalInBackground: true })
  expect(typeof options?.refetchOnMount).toBe('function')
  expect(typeof options?.refetchInterval).toBe('function')
  const activeQuery = client.getQueryCache().find({ queryKey: QK.newsFeed })
  const refetchOnMount = options?.refetchOnMount as (query: unknown) => boolean
  const refetchInterval = options?.refetchInterval as (query: unknown) => number
  expect(refetchOnMount(activeQuery)).toBe(false)
  expect(refetchInterval(activeQuery)).toBe(120_000)
  client.setQueryData(QK.newsFeed, { ...state.data, refreshing: true })
  expect(refetchInterval(activeQuery)).toBe(15_000)
})

it('hydrates a browser-reload snapshot without persisting private associations', async () => {
  await render()
  expect(state.refreshes).toEqual([false])
  expect(readNewsFeedSessionCache()?.items).toHaveLength(2)
  await act(async () => root.render(null))
  client.clear()
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  await render('/news?scope=mine')

  expect(state.refreshes).toEqual([false])
  expect(host.querySelector('[role="status"]')?.textContent).toContain('缓存快照，个股持仓关联待更新')
  expect(host.textContent).toContain('快讯缓存已恢复，个股持仓关联待更新')
  expect(host.textContent).not.toContain('没有符合当前筛选')
})

it('refetches in-memory feed data from a previous Beijing day on mount', async () => {
  const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000)
  const yesterdayKey = beijingNewsDay(yesterday)
  const previousFeed = { ...state.data, today: yesterdayKey }
  expect(writeNewsFeedSessionCache(previousFeed, yesterday)).toBe(true)
  client.setQueryData(QK.newsFeed, previousFeed)

  await render()

  expect(state.refreshes).toEqual([false])
  expect(client.getQueryData<NewsFeedResponse>(QK.newsFeed)?.today).toBe(state.data.today)
})

it('keeps cached feed data and storage after a background refresh fails', async () => {
  expect(writeNewsFeedSessionCache(state.data)).toBe(true)
  const previous = sessionStorage.getItem('stockfund.news-feed.public.v1')
  state.feedError = '后台快讯检查失败'
  await render()
  expect(state.refreshes).toEqual([])

  await act(async () => { await client.refetchQueries({ queryKey: QK.newsFeed, type: 'active' }) })

  expect(state.refreshes).toEqual([false])
  expect(host.querySelectorAll('article')).toHaveLength(2)
  expect(client.getQueryState(QK.newsFeed)?.error).toMatchObject({ message: '后台快讯检查失败' })
  expect(sessionStorage.getItem('stockfund.news-feed.public.v1')).toBe(previous)
  expect(readNewsFeedSessionCache()?.items).toHaveLength(2)
})

it('renders the live feed when browser session storage access is blocked', async () => {
  const descriptor = Object.getOwnPropertyDescriptor(window, 'sessionStorage')
  Object.defineProperty(window, 'sessionStorage', {
    configurable: true,
    get: () => { throw new DOMException('blocked', 'SecurityError') },
  })
  try {
    await render()
    expect(state.refreshes).toEqual([false])
    expect(host.querySelectorAll('article')).toHaveLength(2)
  } finally {
    if (descriptor) Object.defineProperty(window, 'sessionStorage', descriptor)
    else delete (window as unknown as { sessionStorage?: Storage }).sessionStorage
  }
})

it('filters category with counts and changes view', async () => {
  await render('/news?view=serenity')
  await click('风险·负面 1')
  expect(host.querySelectorAll('article')).toHaveLength(1)
  expect(host.querySelector('article')?.textContent).toContain('遭处罚')
  await click('个股')
  expect(host.querySelectorAll('article')).toHaveLength(1)
  expect(host.querySelector('article')?.textContent).toContain('工商银行')
})

it('searches and source filters are honored on direct refresh', async () => {
  await render('/news?source=cls&q=工商银行')
  expect(host.querySelectorAll('article')).toHaveLength(1)
  await click('刷新快讯')
  expect(state.refreshes).toContain(true)
})

it('mine filter uses registered symbols rather than all news', async () => {
  await render('/news?scope=mine')
  expect(host.querySelectorAll('article')).toHaveLength(1)
  await click('工商银行 601398.SH')
  expect(host.textContent).toContain('601398.SH预览')
})

it('shows unconfigured model and can close the AI panel', async () => {
  await render()
  await click('解读')
  expect(state.analyses).toEqual(['1'.repeat(24)])
  expect(host.textContent).toContain('gpt-6.1-sol · xhigh')
  expect(host.textContent).toContain('请先配置 AI')
  expect(host.querySelector('a[href="/settings?tab=ai"]')).not.toBeNull()
  await click('关闭解读')
  expect(host.querySelector('[role="dialog"]')).toBeNull()
})

it('moves focus into a nonmodal report and Escape closes it', async () => {
  await render()
  await click('解读')
  expect(host.querySelector('[role="dialog"]')?.getAttribute('aria-modal')).toBe('false')
  expect(document.activeElement).toBe(button('关闭解读'))
  await act(async () => button('关闭解读').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
  expect(host.querySelector('[role="dialog"]')).toBeNull()
})

it('keeps a busy response inline instead of opening an unpersisted report', async () => {
  state.busy = true
  await render()
  await click('解读')
  expect(host.querySelector('[role="dialog"]')).toBeNull()
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('已有 AI 解读正在运行')
})

it('empty data explains unavailability instead of displaying fake news', async () => {
  state.data.items = []; state.data.summary.today_count = 0; state.data.summary.total_count = 0
  await render()
  expect(host.textContent).toContain('暂无有效快讯')
  expect(button('AI 今日研判').disabled).toBe(true)
})
