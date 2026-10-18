// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { storage } from '@/lib/storage'
import type { IndustryMode } from './types'
import { GlobalIndustryPanel } from './GlobalIndustryPanel'

const mocks = vi.hoisted(() => ({ globalIndustries: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { globalIndustries: mocks.globalIndustries } }))

function response(mode: IndustryMode) {
  const market = mode === 'us' ? 'us' : 'cn'
  return {
    market, mode, basis: 'equal_weight_representative_basket',
    source: market === 'us' ? 'tencent_public' : 'panel_stock_snapshot',
    currency: market === 'us' ? 'USD' : 'CNY', date: '2026-10-16', fetched_at_ms: null,
    loading: false, stale: false, error: null, disclaimer: '代表股票篮子', auto_rule: '测试规则',
    themes: [{ id: 'ai_compute', name: 'AI算力', note: '代表篮子', change_pct: 0.0366,
      covered: 1, total: 2, members: [
        { symbol: 'NVDA', name: '英伟达', price: 100, change_pct: .0366, quote_at_ms: null, quote_date: '2026-10-16', basis: 'source_reported_date', included: true },
        { symbol: 'AMD', name: '超微', price: null, change_pct: null, quote_at_ms: null, quote_date: null, basis: 'source_reported_date', included: false },
      ] }],
  }
}

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  sessionStorage.clear()
  mocks.globalIndustries.mockReset().mockImplementation(async (mode: IndustryMode) => response(mode))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient.clear()
  host.remove()
})

async function settle() {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)) })
}

async function renderPanel() {
  await act(async () => root.render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}><GlobalIndustryPanel /></QueryClientProvider>
    </MemoryRouter>,
  ))
  await settle()
}

async function chooseMode(label: string) {
  const group = host.querySelector('[aria-label="全球产业市场"]')
  const button = [...(group?.querySelectorAll('button') ?? [])].find(candidate => candidate.textContent === label)
  expect(button).toBeTruthy()
  await act(async () => button!.click())
  await settle()
}

it('starts in automatic mode and persists manual A-share and US overrides', async () => {
  await renderPanel()

  const group = host.querySelector('[aria-label="全球产业市场"]')!
  const pressed = (label: string) => [...group.querySelectorAll('button')]
    .find(button => button.textContent === label)?.getAttribute('aria-pressed')

  expect(pressed('自动')).toBe('true')
  expect(mocks.globalIndustries).toHaveBeenCalledWith('auto', expect.any(AbortSignal))
  expect(host.textContent).toContain('A股代表篮子')
  expect(host.textContent).toContain('3.66%')

  await chooseMode('美股')
  expect(pressed('美股')).toBe('true')
  expect(mocks.globalIndustries).toHaveBeenCalledWith('us', expect.any(AbortSignal))
  expect(storage.globalIndustryView.get({})).toMatchObject({ mode: 'us' })

  await chooseMode('A股')
  expect(pressed('A股')).toBe('true')
  expect(mocks.globalIndustries).toHaveBeenCalledWith('cn', expect.any(AbortSignal))
  expect(storage.globalIndustryView.get({})).toMatchObject({ mode: 'cn' })

  await chooseMode('自动')
  expect(pressed('自动')).toBe('true')
  expect(mocks.globalIndustries).toHaveBeenCalledWith('auto', expect.any(AbortSignal))
  expect(storage.globalIndustryView.get({})).toMatchObject({ mode: 'auto' })
})

it('expands constituents with names, real coverage and a market-specific chart link', async () => {
  storage.globalIndustryView.set({ mode: 'us' })
  await renderPanel()
  const tile = host.querySelector<HTMLButtonElement>('button[aria-controls="global-industry-members"]')!
  await act(async () => tile.click())
  const details = host.querySelector('#global-industry-members')!
  expect(tile.getAttribute('aria-expanded')).toBe('true')
  expect(details.textContent).toContain('英伟达')
  expect(details.textContent).toContain('报价缺失 · 未计入')
  expect(details.querySelector('a')?.getAttribute('href')).toBe('/?market=us&symbol=NVDA')
  await act(async () => tile.click())
  expect(host.querySelector('#global-industry-members')).toBeNull()
})
