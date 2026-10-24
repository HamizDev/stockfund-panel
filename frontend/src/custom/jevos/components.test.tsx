// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { JevosReview, JevosStatus } from './types'
import { JevosNewsReview, JevosNewsStatus, JevosStrategyShadow, useJevosStatus } from './components'

const state = vi.hoisted(() => ({ status: {} as JevosStatus, calls: 0 }))
vi.mock('@/lib/api', () => ({ api: { jevosStatus: async () => { state.calls++; return state.status } } }))

function review(overrides: Partial<JevosReview> = {}): JevosReview {
  return {
    kind: 'news', id: 'article-1', input_key: 'a'.repeat(64), model: 'jevos-v5', fingerprint: 'b'.repeat(64),
    generated_at_ms: 1_800_000_000_000, source_at_ms: null, label: 'positive_clue', original_signal: null,
    probabilities: { positive: 0.91, risk: 0.04, material: 0.83 }, related_names: ['示例ETF'], ...overrides,
  }
}

function status(overrides: Partial<JevosStatus> = {}): JevosStatus {
  return {
    mode: 'shadow', status: 'ready', model: 'jevos-v5', fingerprint: 'b'.repeat(64), last_check_ms: 1_800_000_000_000,
    queued: 1, completed: 1, news: {}, strategies: {}, strategy_history: [], ...overrides,
  }
}

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  state.calls = 0
  state.status = status()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient.clear()
  host.remove()
})

async function render(node: ReactNode) {
  await act(async () => root.render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

it('shows local service state and separates model probabilities from win rate', async () => {
  const item = review()
  await render(<><JevosNewsStatus status={status({ news: { [item.id]: item } })} isLoading={false} isError={false} /><JevosNewsReview review={item} /></>)
  expect(host.textContent).toContain('Jevos 本地初筛')
  expect(host.textContent).toContain('本机服务就绪')
  expect(host.textContent).toContain('偏利好')
  expect(host.textContent).toContain('模型判断概率（不是胜率）')
  expect(host.textContent).toContain('关注 / 登记持仓')
  expect(host.textContent).toContain('不代表相关标的一定受益')
})

it('shows news and strategy queue counts separately and labels completed records as cache', async () => {
  const item = review()
  await render(<JevosNewsStatus status={status({ queued: 64, completed: 400,
    queued_by_kind: { news: 12, strategy: 52 }, news: { [item.id]: item } })} isLoading={false} isError={false} />)

  expect(host.textContent).toContain('快讯结果 1 条')
  expect(host.textContent).toContain('快讯待处理 12 条')
  expect(host.textContent).toContain('策略待处理 52 条')
  expect(host.textContent).toContain('本机缓存 400 条')
  expect(host.textContent).not.toContain('完成 400')
  expect(host.textContent).not.toContain('队列 64')
})

it('falls back to the total queue count when an older API omits per-kind counts', async () => {
  await render(<JevosNewsStatus status={status({ queued: 64, completed: 400, queued_by_kind: undefined })}
    isLoading={false} isError={false} />)

  expect(host.textContent).toContain('队列 64')
  expect(host.textContent).toContain('本机缓存 400 条')
})

it('shows exact news evidence and weak classification without presenting calibration as accuracy', async () => {
  await render(<JevosNewsReview review={review({ label: 'neutral', method: 'choice-v2', tentative: true,
    confidence: 0.45, reason: '原文未见明确方向变化：召开例行股东大会', evidence: ['召开例行股东大会'],
    probabilities: { positive: 0.1, negative: 0.1, neutral: 0.6, mixed: 0.1, uncertain: 0.1 } })} />)
  expect(host.textContent).toContain('中性')
  expect(host.textContent).toContain('判断较弱')
  expect(host.textContent).toContain('原文：召开例行股东大会')
  expect(host.textContent).toContain('模型区分度 45%（未经市场准确率校准）')
})

it('marks an old strategy review as historical against the current closed candle', async () => {
  const old = review({ kind: 'strategy', id: 'eth-bot', label: 'support', original_signal: 'long', source_at_ms: 1_799_000_000_000,
    probabilities: { supports_signal: 0.91, conflict: 0.03, sufficient: 0.88 }, related_names: [] })
  await render(<JevosStrategyShadow status={status({ strategies: { 'eth-bot': old }, strategy_history: [old] })} isLoading={false} isError={false}
    bots={[{ id: 'eth-bot', name: 'ETH 均线趋势', last_bar_ms: 1_799_100_000_000, last_signal: 'long' }]} />)
  expect(host.textContent).toContain('本地辅助，未参与下单')
  expect(host.textContent).toContain('ETH 均线趋势')
  expect(host.textContent).toContain('基于旧 K 线的历史意见')
  expect(host.textContent).toContain('最近 1 条策略意见')
  expect(host.textContent).toContain('模型判断概率（非胜率）')
})

it('shows a safe unavailable state without affecting surrounding news or trading UI', async () => {
  await render(<><JevosNewsStatus status={undefined} isLoading={false} isError={true} /><JevosStrategyShadow status={undefined} isLoading={false} isError={true} bots={[]} /></>)
  expect(host.textContent).toContain('状态暂不可用')
  expect(host.textContent).toContain('策略监控和模拟交易状态不受影响')
})

function QueryProbe() {
  const query = useJevosStatus()
  return <span>{query.data?.status ?? 'loading'}</span>
}

it('uses one shared status query that does not poll while the page is in the background', async () => {
  await render(<QueryProbe />)
  expect(state.calls).toBe(1)
  const query = queryClient.getQueryCache().find({ queryKey: ['jevos-status'] })
  expect(query?.queryKey).toEqual(['jevos-status'])
  const options = query?.options as unknown as { refetchInterval?: number; refetchIntervalInBackground?: boolean } | undefined
  expect(options?.refetchInterval).toBe(10_000)
  expect(options?.refetchIntervalInBackground).toBe(false)
})
