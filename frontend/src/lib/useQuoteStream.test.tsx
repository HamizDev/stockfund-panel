// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QK } from './queryKeys'
import { useQuoteStream } from './useQuoteStream'

vi.mock('@/components/Toast', () => ({ toast: vi.fn() }))
vi.mock('@/components/AlertToast', () => ({ pushAlertToasts: vi.fn() }))
vi.mock('./reviewStore', () => ({ feedReviewEvent: vi.fn() }))

class Stream {
  static current: Stream
  listeners = new Map<string, () => void>()
  onopen = null
  onerror = null
  constructor() { Stream.current = this }
  addEventListener(event: string, callback: () => void) { this.listeners.set(event, callback) }
  close() {}
}

beforeEach(() => {
  vi.stubGlobal('EventSource', Stream)
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
})
afterEach(() => vi.unstubAllGlobals())

async function mount(pages?: Record<string, boolean>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(QK.dashboardInit(), { value: 'latest' })
  client.setQueryData(QK.dashboardInit('2026-10-15'), { value: 'history' })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  function Consumer() { useQuoteStream(true, pages); return null }
  const Wrapper = wrapper
  const host = document.createElement('div')
  const root = createRoot(host)
  await act(async () => root.render(<Wrapper><Consumer /></Wrapper>))
  const hook = { unmount: async () => { await act(async () => root.unmount()); client.clear() } }
  return { client, hook }
}

it('行情到达使新首页聚合查询失效，历史日期保持原快照', async () => {
  const { client, hook } = await mount()
  await act(async () => Stream.current.listeners.get('quotes_updated')?.())
  expect(client.getQueryState(QK.dashboardInit())?.isInvalidated).toBe(true)
  expect(client.getQueryState(QK.dashboardInit('2026-10-15'))?.isInvalidated).toBe(false)
  await hook.unmount()
})

it('首页沿用总览的实时刷新开关', async () => {
  const { client, hook } = await mount({ 'overview-market': false })
  await act(async () => Stream.current.listeners.get('quotes_updated')?.())
  expect(client.getQueryState(QK.dashboardInit())?.isInvalidated).toBe(false)
  await hook.unmount()
})

it('五档修正到达后也刷新当前首页封板统计', async () => {
  const { client, hook } = await mount()
  await act(async () => Stream.current.listeners.get('depth_updated')?.())
  expect(client.getQueryState(QK.dashboardInit())?.isInvalidated).toBe(true)
  expect(client.getQueryState(QK.dashboardInit('2026-10-15'))?.isInvalidated).toBe(false)
  await hook.unmount()
})
