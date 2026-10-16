// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { FreeAuctionCard } from './FreeAuctionCard'

vi.mock('@/components/financials/StockFinancialSearch', () => ({
  StockFinancialSearch: ({ onSelect }: { onSelect: (symbol: string, name: string) => void }) =>
    <button onClick={() => onSelect('518880.SH', '黄金ETF')}>选择黄金</button>,
}))
const fetchMock = vi.fn<typeof fetch>()
let root: Root
let host: HTMLDivElement
function evidence(url: string) {
  const query = new URL(url, 'http://localhost').searchParams
  return { symbol: query.get('symbol'), trade_date: query.get('trade_date'), source: 'test', state: 'ok',
    cached: true, stale: false, observed_at: null, points: [], opening_match: null,
    reference_price: null, open_change_pct: null, notes: [], errors: [] }
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  sessionStorage.clear(); fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockImplementation(async input => new Response(JSON.stringify(String(input).includes('/status')
    ? { available: true, source: 'test', mode: 'on_demand' } : evidence(String(input))), { status: 200 }))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals() })
async function settle() { for (let i = 0; i < 3; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) }) }
async function render() { await act(async () => root.render(<FreeAuctionCard />)); await settle() }
function button(text: string) { return [...host.querySelectorAll('button')].find(button => button.textContent?.includes(text))! }
it('restores stock, date and phase and only reads existing cache on return', async () => {
  await render()
  await act(async () => button('选择黄金').click()); await settle()
  await act(async () => button('收盘竞价').click())
  const saved = JSON.parse(sessionStorage.getItem('page-view:auction')!)
  expect(saved.symbol).toBe('518880.SH'); expect(saved.phase).toBe('closing')
  await act(async () => root.render(null)); await render()
  expect(button('收盘竞价').getAttribute('aria-pressed')).toBe('true')
  expect(host.textContent).toContain('黄金ETF')
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('symbol=518880.SH'))).toBe(true)
  expect(fetchMock.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
})
it('ignores a delayed result for the previous stock', async () => {
  let finish!: (response: Response) => void
  fetchMock.mockImplementation(async input => String(input).includes('symbol=511360.SH')
    ? new Promise<Response>(resolve => { finish = resolve })
    : new Response(JSON.stringify(String(input).includes('/status') ? { available: true, source: 'test' } : evidence(String(input)))))
  await render(); await act(async () => button('选择黄金').click()); await settle()
  await act(async () => finish(new Response(JSON.stringify({ ...evidence('/?symbol=511360.SH&trade_date=2026-10-15'), source: 'wrong-stock' }))))
  await settle()
  expect(host.textContent).toContain('黄金ETF')
  expect(host.textContent).not.toContain('wrong-stock')
})
