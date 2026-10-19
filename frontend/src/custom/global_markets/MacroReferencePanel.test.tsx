// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { MacroReferencePanel } from './MacroReferencePanel'
import type { MacroReference } from './types'

const mocks = vi.hoisted(() => ({ macroReference: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { macroReference: mocks.macroReference } }))
let host: HTMLDivElement
let root: Root
let client: QueryClient
const response = (market: 'cn' | 'us'): MacroReference => ({
  market, currency: market === 'us' ? 'USD' : 'CNY', unit: 'percent', frequency: 'business_daily',
  date: '2026-10-16', previous_date: '2026-10-15', source: market === 'us' ? '美国财政部' : '中国货币网',
  source_url: 'https://home.treasury.gov/', fetched_at_ms: 1, cached: false, stale: false, error: null,
  spread_bp: market === 'us' ? 35 : null,
  items: market === 'us' ? [
    { id: '2y', label: '2年', value: 4.74, previous_value: 4.77, change_bp: -3 },
    { id: '10y', label: '10年', value: 5.09, previous_value: 5.1, change_bp: -1 },
    { id: '30y', label: '30年', value: null, previous_value: null, change_bp: null },
  ] : [{ id: 'FR001', label: '隔夜 FR001', value: 1.32, previous_value: 1.33, change_bp: -1 }],
})
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  mocks.macroReference.mockReset().mockImplementation(async market => response(market))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove() })
async function render(market: 'cn' | 'us') {
  await act(async () => root.render(<QueryClientProvider client={client}><MacroReferencePanel market={market} /></QueryClientProvider>))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
}
it('shows official dates, percentage units, bp changes and a labeled yield curve', async () => {
  await render('us')
  expect(host.textContent).toContain('5.09%')
  expect(host.textContent).toContain('较前次 -1.0 bp')
  expect(host.textContent).toContain('10年 − 2年：+35.0 bp')
  expect(host.textContent).toContain('2026-10-16 / 前次 2026-10-15')
  expect(host.querySelector('svg[role="img"]')?.getAttribute('aria-label')).toContain('单位百分比')
  expect(host.textContent).not.toContain('NaN')
})
it('keeps market caches separate and returning to a page reuses its existing daily reference', async () => {
  await render('us'); await render('cn'); await render('us')
  expect(mocks.macroReference).toHaveBeenCalledTimes(2)
  expect(host.textContent).not.toContain('FR001')
})
it('warns on a stale cached response and preserves its original date', async () => {
  mocks.macroReference.mockResolvedValue({ ...response('cn'), stale: true, cached: true, error: '官方源读取失败' })
  await render('cn')
  expect(host.textContent).toContain('旧缓存 · 官方源读取失败')
  expect(host.textContent).toContain('2026-10-16')
  expect(host.textContent).toContain('1.32%')
})
it('isolates source failure without showing zero rates', async () => {
  mocks.macroReference.mockRejectedValue(new Error('官方利率暂不可用'))
  await render('us')
  expect(host.textContent).toContain('官方利率暂不可用')
  expect(host.textContent).not.toContain('0.00%')
})
