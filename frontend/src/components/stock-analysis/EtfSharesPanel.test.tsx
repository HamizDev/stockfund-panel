// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { EtfSharesPanel } from './EtfSharesPanel'

const mocks = vi.hoisted(() => ({ etfShares: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { etfShares: mocks.etfShares } }))
let host: HTMLDivElement, root: Root, client: QueryClient
const result = (symbol: string) => ({ symbol, name: symbol === '515880.SH' ? '通信ETF' : '创业板ETF',
  date: '2026-10-16', shares_10k: 123456.78, source: symbol.endsWith('SH') ? '上交所' : '深交所',
  source_url: 'https://www.sse.com.cn/', cached: false, stale: false, error: null })
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  mocks.etfShares.mockReset().mockImplementation(async symbol => result(symbol))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove() })
async function render(symbol = '515880.SH', date: string | null = null) {
  await act(async () => root.render(<QueryClientProvider client={client}><EtfSharesPanel symbol={symbol} date={date} /></QueryClientProvider>))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
}
it('prioritizes the ETF name, official date and explicit ten-thousand-unit basis', async () => {
  await render()
  expect(host.textContent).toContain('通信ETF')
  expect(host.textContent).toContain('123,456.78万份')
  expect(host.textContent).toContain('数据日 2026-10-16')
  expect(host.textContent).toContain('非基金净资产、成交量或资金净流入')
  expect(host.textContent).not.toContain('上交所')
  expect(host.textContent).not.toContain('深交所')
  expect(host.querySelector('a')).toBeNull()
})
it('queries selected dates separately and never flashes another symbol', async () => {
  await render(); await render('159915.SZ', '2026-10-15')
  expect(mocks.etfShares).toHaveBeenLastCalledWith('159915.SZ', '2026-10-15', expect.any(AbortSignal))
  expect(host.textContent).toContain('创业板ETF')
  expect(host.textContent).not.toContain('通信ETF')
})
it('shows an official unavailable error without inventing zero shares', async () => {
  mocks.etfShares.mockResolvedValue({ ...result('515880.SH'), shares_10k: null, date: null, error: '尚未发布' })
  await render()
  expect(host.textContent).toContain('尚未发布')
  expect(host.textContent).not.toContain('0万份')
  expect(host.textContent).not.toContain('上交所')
  expect(host.textContent).not.toContain('深交所')
  expect(host.querySelector('a')).toBeNull()
})
