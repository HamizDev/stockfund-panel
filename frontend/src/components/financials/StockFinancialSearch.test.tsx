// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StockFinancialSearch } from './StockFinancialSearch'

const calls = vi.hoisted(() => ({ search: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { instrumentSearch: calls.search } }))

let host: HTMLDivElement
let root: Root
let query: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  query = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  calls.search.mockReset().mockResolvedValue({ results: [
    { symbol: '000300.SH', name: '沪深300', asset_type: 'index' },
  ] })
})

afterEach(async () => {
  await act(async () => root.unmount())
  query.clear()
  host.remove()
})

async function settle() {
  for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

it('passes the result asset type as an optional third selection argument', async () => {
  const onSelect = vi.fn()
  await act(async () => root.render(
    <QueryClientProvider client={query}><StockFinancialSearch onSelect={onSelect} assetTypes="stock,etf,index" value="沪深300" onValueChange={() => {}} /></QueryClientProvider>,
  ))
  const input = host.querySelector<HTMLInputElement>('input')!
  await act(async () => input.focus())
  await settle()
  await act(async () => host.querySelector<HTMLButtonElement>('button')!.click())
  expect(onSelect).toHaveBeenCalledWith('000300.SH', '沪深300', 'index')
  expect(calls.search).toHaveBeenCalledTimes(1)
})
