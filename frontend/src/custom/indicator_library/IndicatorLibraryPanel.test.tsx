// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { QK } from '@/lib/queryKeys'
import { IndicatorLibraryPanel } from './IndicatorLibraryPanel'
import type { IndicatorCatalog, IndicatorSearch, PublicScript } from './types'

const mocks = vi.hoisted(() => ({ catalog: vi.fn(), search: vi.fn(), source: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { indicatorCatalog: mocks.catalog, indicatorSearch: mocks.search, indicatorSource: mocks.source } }))
const catalog: IndicatorCatalog = {
  topics: [{ id: 'supertrend', name: '超级趋势', aliases: [] }],
  recipes: [{ id: 'dmi_confirm', name: 'DMI 连续确认', kind: '趋势确认', description: '只观察已收盘数据',
    inputs: 'OHLC', entry: '下一根才成交', exit: '方向反转', caution: '不代表收益概率' }],
}
const item = (id = 'PUB;abc'): PublicScript => ({ id, name: `指标 ${id}`, author: 'Researcher', kind: 'indicator',
  version: '1', description: '一份公开研究', url: 'https://www.tradingview.com/script/Abc-test/', source_cached: true, cached_version: '1.0' })
const result = (...items: PublicScript[]): IndicatorSearch => ({ items, page: 1, has_more: false,
  offline: false, cached: false, fetched_at_ms: 1792610000000, topic: 'supertrend', note: '' })
let root: Root
let host: HTMLDivElement
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  sessionStorage.clear()
  mocks.catalog.mockReset().mockResolvedValue(catalog)
  mocks.search.mockReset().mockResolvedValue(result(item(), item('PUB;def')))
  mocks.source.mockReset().mockImplementation(async (id: string) => ({ id, name: id, version: '1.0', pine_version: 6,
    source: `//@version=6\n// ${id}\n<script>alert('text only')</script>`, bytes: 75, fetched_at_ms: 1792610000000, cached: false }))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); sessionStorage.clear() })
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) }) }
async function render() { await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter><IndicatorLibraryPanel /></MemoryRouter></QueryClientProvider>)); await settle() }
function button(label: string) { return [...host.querySelectorAll<HTMLButtonElement>('button')].find(node => node.textContent === label)! }
async function click(label: string) { await act(async () => button(label).click()); await settle() }
async function input(label: string, value: string) {
  const node = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value)
    node.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

it('opens existing rule research without online discovery or a source request', async () => {
  await render()
  expect(host.textContent).toContain('DMI 连续确认')
  expect(host.textContent).toContain('下一根才成交')
  expect(mocks.search).not.toHaveBeenCalled()
  expect(mocks.source).not.toHaveBeenCalled()
})

it('does not query on entering online discovery; a Chinese topic requests only its list', async () => {
  await render(); await click('在线发现')
  expect(mocks.search).not.toHaveBeenCalled()
  await click('超级趋势')
  expect(mocks.search).toHaveBeenCalledTimes(1)
  expect(mocks.search.mock.calls[0][0]).toEqual({ q: '超级趋势', author: '', kind: 'all', page: 1, offline: false })
  expect(mocks.source).not.toHaveBeenCalled()
})

it('submits author/type context and does not query while editing', async () => {
  await render(); await click('在线发现')
  await input('指标搜索关键词', '回归'); await input('指标作者', 'Researcher')
  expect(mocks.search).not.toHaveBeenCalled()
  const select = host.querySelector<HTMLSelectElement>('select[aria-label="脚本类型"]')!
  await act(async () => { select.value = 'strategy'; select.dispatchEvent(new Event('change', { bubbles: true })) })
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  await settle()
  expect(mocks.search.mock.calls[0][0]).toEqual({ q: '回归', author: 'Researcher', kind: 'strategy', page: 1, offline: false })
})

it('only fetches source on demand and renders markup as literal text', async () => {
  await render(); await click('在线发现'); await click('超级趋势')
  await click('按需查看源码')
  expect(mocks.source).toHaveBeenCalledTimes(1)
  expect(mocks.source.mock.calls[0].slice(0, 3)).toEqual(['PUB;abc', '1', false])
  expect(host.querySelector('pre code')?.textContent).toContain('<script>')
  expect(host.querySelector('script')).toBeNull()
  expect(host.textContent).toContain('Pine 6')
})

it('keeps the last view and fresh data across a route remount without refetching', async () => {
  await render(); await click('在线发现'); await click('超级趋势'); await click('按需查看源码')
  await act(async () => root.render(<div />)); await render()
  expect(host.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('在线发现')
  expect(host.querySelector('pre')?.textContent).toContain('PUB;abc')
  expect(mocks.catalog).toHaveBeenCalledTimes(1)
  expect(mocks.search).toHaveBeenCalledTimes(1)
  expect(mocks.source).toHaveBeenCalledTimes(1)
})

it('releases the previous source when selecting another script without leaking it into a failed result', async () => {
  await render(); await click('在线发现'); await click('超级趋势'); await click('按需查看源码')
  mocks.source.mockRejectedValueOnce(new Error('此脚本未提供公开源码'))
  const second = [...host.querySelectorAll<HTMLButtonElement>('button')].filter(node => node.textContent === '按需查看源码')[1]
  await act(async () => second.click()); await settle()
  expect(host.querySelector('pre')).toBeNull()
  expect(host.textContent).toContain('此脚本未提供公开源码')
  expect(client.getQueryCache().find({ queryKey: QK.indicatorSource('PUB;abc', '1', false) })).toBeUndefined()
})

it('offline source uses the cached revision and never substitutes the online latest', async () => {
  await render(); await click('离线检索'); await click('查看缓存源码')
  expect(mocks.search.mock.calls[0][0].offline).toBe(true)
  expect(mocks.source.mock.calls[0].slice(0, 3)).toEqual(['PUB;abc', '1.0', true])
})

it('applies the visible keyword, author and kind when entering offline search', async () => {
  await render(); await click('在线发现')
  await input('指标搜索关键词', '均值回归'); await input('指标作者', 'Researcher')
  const select = host.querySelector<HTMLSelectElement>('select[aria-label="脚本类型"]')!
  await act(async () => { select.value = 'strategy'; select.dispatchEvent(new Event('change', { bubbles: true })) })
  await click('离线检索')
  expect(mocks.search.mock.calls[0][0]).toEqual({ q: '均值回归', author: 'Researcher', kind: 'strategy', page: 1, offline: true })
})

it('shows an honest empty offline state and disables source actions without a cache', async () => {
  mocks.search.mockResolvedValueOnce(result())
  await render(); await click('离线检索')
  expect(host.textContent).toContain('没有匹配的已缓存脚本')
  expect(mocks.source).not.toHaveBeenCalled()
  mocks.search.mockResolvedValueOnce(result({ ...item(), source_cached: false }))
  await act(async () => { await client.invalidateQueries({ queryKey: ['indicator-library', 'search'] }) }); await settle()
  expect(button('查看缓存源码').disabled).toBe(true)
})

it('shows a failed online search without stale results from a different query', async () => {
  await render(); await click('在线发现'); await click('超级趋势')
  mocks.search.mockRejectedValueOnce(new Error('公开脚本访问受限'))
  await input('指标搜索关键词', 'macd')
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))); await settle()
  expect(host.textContent).toContain('公开脚本访问受限')
  expect(host.textContent).not.toContain('指标 PUB;abc')
})

it('rejects an unsafe external link and malformed stored page selection', async () => {
  sessionStorage.setItem('page-view:indicator-library', JSON.stringify({ ...INITIAL_FOR_TEST, selected: { id: '../bad', version: '1', name: 'bad' } }))
  mocks.search.mockResolvedValueOnce(result({ ...item(), url: 'javascript:alert(1)' }))
  await render()
  expect(host.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('已有研究')
  await click('在线发现'); await click('超级趋势')
  expect(host.querySelector('a[href^="javascript:"]')).toBeNull()
})
const INITIAL_FOR_TEST = { mode: 'online', input: '', author: '', kind: 'all', search: null, selected: null }
