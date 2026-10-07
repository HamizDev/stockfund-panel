// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import type { NewsFeedResponse } from '@/lib/api'
import { NewsPage } from './NewsPage'

const state = vi.hoisted(() => ({ data: {} as NewsFeedResponse, refreshes: [] as boolean[], analyses: [] as string[], busy: false }))
vi.mock('@/components/StockPreviewDialog', () => ({ StockPreviewDialog: ({ symbol }: { symbol: string }) => <div>{symbol}预览</div> }))
vi.mock('@/lib/api', () => ({ api: {
  newsFeed: async (refresh = false) => { state.refreshes.push(refresh); return state.data },
  newsAnalyze: async (id: string) => {
    state.analyses.push(id)
    return { id: 'a'.repeat(32), article_id: id, status: state.busy ? 'busy' : 'unconfigured', error: state.busy ? '已有 AI 解读正在运行' : '请先配置 AI', content: null, model: 'gpt-6.1-sol', reasoning_effort: 'xhigh', generated_at: null }
  },
  newsAnalysis: async () => ({ status: 'failed', error: 'not found' }),
} }))

let host: HTMLDivElement
let root: Root
let client: QueryClient
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  state.refreshes = []; state.analyses = []; state.busy = false
  state.data = {
    today: '2026-10-07', fetched_at: '2026-10-07T10:00:00+08:00', association_status: 'ok', related_symbols: ['601398.SH'],
    sources: [{ source: 'cls', label: '财联社', status: 'ok', fetched_at: '2026-10-07T10:00:00+08:00', count: 1, reason: null },
      { source: 'eastmoney', label: '东财7×24', status: 'unavailable', fetched_at: null, count: 0, reason: '接口不可用' }],
    summary: { today_count: 2, total_count: 2, themes: [{ label: '资本运作', count: 1 }] },
    items: [
      { id: '1'.repeat(24), title: '工商银行回购公告', summary: '公司披露回购计划', published_at: '2026-10-07T10:00:00+08:00',
        origins: [{ source: 'cls', label: '财联社', url: 'https://www.cls.cn/detail/123', published_at: '2026-10-07T10:00:00+08:00' }],
        classifications: { serenity: { category: 'capital', label: '资本运作', score: 18, matched: ['回购'] }, wojianshan: { category: 'capital', label: '资本运作', score: 23, matched: ['回购'] } },
        sentiment: { direction: 'positive', matched: ['回购'] },
        associations: [{ symbol: '601398.SH', name: '工商银行', asset_type: 'stock', direct: true, basis: '正文明确出现完整名称 工商银行' }] },
      { id: '2'.repeat(24), title: '某公司遭处罚', summary: '监管发布调查结果', published_at: '2026-10-07T09:00:00+08:00',
        origins: [{ source: 'eastmoney', label: '东财7×24', url: null, published_at: '2026-10-07T09:00:00+08:00' }],
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
async function click(label: string) { await act(async () => button(label).click()); await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) }) }

it('renders source failure independently, original links and explicit model action', async () => {
  await render()
  expect(host.querySelectorAll('article')).toHaveLength(2)
  expect(host.textContent).toContain('东财7×24 不可用')
  expect(host.textContent).toContain('拉取 尚未取得')
  expect(host.querySelector('a[href="https://www.cls.cn/detail/123"]')?.getAttribute('rel')).toContain('noreferrer')
  expect(state.analyses).toEqual([])
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
