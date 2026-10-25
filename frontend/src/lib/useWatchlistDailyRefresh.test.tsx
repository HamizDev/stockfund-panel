// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useWatchlistDailyRefresh, type WatchlistDailyRefreshRow } from './useWatchlistDailyRefresh'
import type { WatchlistDailySyncResult } from './useWatchlistDailyRefresh'

const { watchlistSyncDaily } = vi.hoisted(() => ({ watchlistSyncDaily: vi.fn() }))
vi.mock('./api', () => ({ api: { watchlistSyncDaily } }))

const completeResult: WatchlistDailySyncResult = {
  requested: ['ETF_A.SH'], rows_written: 1, remaining: [], as_of: '2026-10-23',
}

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient
let latest: ReturnType<typeof useWatchlistDailyRefresh>

function HookProbe({
  rows,
  ready,
  onRefetch,
}: {
  rows: readonly WatchlistDailyRefreshRow[]
  ready: boolean
  onRefetch: () => void | Promise<void>
}) {
  latest = useWatchlistDailyRefresh(rows, ready, onRefetch)
  return <div>
    <span data-testid="pending">{String(latest.isPending)}</span>
    <span data-testid="error">{latest.error ?? ''}</span>
  </div>
}

async function render(rows: readonly WatchlistDailyRefreshRow[], ready = true, onRefetch = vi.fn()) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <HookProbe rows={rows} ready={ready} onRefetch={onRefetch} />
      </QueryClientProvider>,
    )
  })
}

async function settle() {
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, 0))
  })
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  root = createRoot(host)
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  vi.mocked(watchlistSyncDaily).mockReset()
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient.clear()
  vi.restoreAllMocks()
})

describe('useWatchlistDailyRefresh', () => {
  it('automatically requests once for the same missing ETF set', async () => {
    vi.mocked(watchlistSyncDaily).mockResolvedValue(completeResult)
    const rows = [
      { symbol: 'ETF_A.SH', asset_type: 'etf', close: null },
      { symbol: 'ETF_B.SH', asset_type: 'etf', close: null },
    ]
    const onRefetch = vi.fn()

    await render(rows, true, onRefetch)
    await settle()
    await render([...rows].reverse(), true, onRefetch)
    await settle()

    expect(watchlistSyncDaily).toHaveBeenCalledTimes(1)
    expect(watchlistSyncDaily).toHaveBeenCalledWith(false)
    expect(onRefetch).toHaveBeenCalledTimes(1)
  })

  it('treats invalid ETF closes as missing, matching the backend check', async () => {
    vi.mocked(watchlistSyncDaily).mockResolvedValue(completeResult)

    await render([
      { symbol: 'ETF_A.SH', asset_type: 'etf', close: 0 },
      { symbol: 'ETF_B.SH', asset_type: 'etf', close: Number.NaN },
      { symbol: 'ETF_C.SH', asset_type: 'etf', close: Number.POSITIVE_INFINITY },
    ])
    await settle()

    expect(watchlistSyncDaily).toHaveBeenCalledOnce()
    expect(watchlistSyncDaily).toHaveBeenCalledWith(false)
  })

  it('does not auto-sync when only stock or index rows are missing', async () => {
    await render([
      { symbol: 'STOCK_A.SH', asset_type: 'stock', close: null },
      { symbol: 'INDEX_A.SH', asset_type: 'index', close: null },
    ])
    await settle()

    expect(watchlistSyncDaily).not.toHaveBeenCalled()
  })

  it('waits until the watchlist is ready before auto-syncing', async () => {
    vi.mocked(watchlistSyncDaily).mockResolvedValue(completeResult)
    const rows = [{ symbol: 'ETF_A.SH', asset_type: 'etf', close: null }]

    await render(rows, false)
    await settle()
    expect(watchlistSyncDaily).not.toHaveBeenCalled()

    await render(rows, true)
    await settle()
    expect(watchlistSyncDaily).toHaveBeenCalledOnce()
    expect(watchlistSyncDaily).toHaveBeenCalledWith(false)
  })

  it('forces manual ETF refresh, invalidates both prefixes, and uses the latest refetch callback', async () => {
    vi.mocked(watchlistSyncDaily).mockResolvedValue(completeResult)
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue([] as never)
    const oldRefetch = vi.fn()
    const latestRefetch = vi.fn()
    const rows = [{ symbol: 'ETF_A.SH', asset_type: 'etf', close: 4.1 }]

    await render(rows, true, oldRefetch)
    await render(rows, true, latestRefetch)
    await act(async () => latest.refresh())
    await settle()

    expect(watchlistSyncDaily).toHaveBeenCalledOnce()
    expect(watchlistSyncDaily).toHaveBeenCalledWith(true)
    expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
      ['watchlist-enriched'], ['kline-batch'],
    ])
    expect(latestRefetch).toHaveBeenCalledOnce()
    expect(oldRefetch).not.toHaveBeenCalled()
  })

  it('only refetches when manual refresh has no ETFs', async () => {
    const onRefetch = vi.fn()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    await render([{ symbol: 'STOCK_A.SH', asset_type: 'stock', close: 12.3 }], true, onRefetch)
    await act(async () => latest.refresh())
    await settle()

    expect(watchlistSyncDaily).not.toHaveBeenCalled()
    expect(invalidate).not.toHaveBeenCalled()
    expect(onRefetch).toHaveBeenCalledOnce()
  })

  it('shows failures and does not auto-retry the same missing set', async () => {
    vi.mocked(watchlistSyncDaily).mockRejectedValue(new Error('sync temporarily unavailable'))
    const rows = [{ symbol: 'ETF_A.SH', asset_type: 'etf', close: null }]

    await render(rows)
    await settle()
    await render([{ ...rows[0] }])
    await settle()

    expect(watchlistSyncDaily).toHaveBeenCalledOnce()
    expect(host.querySelector('[data-testid="error"]')?.textContent)
      .toContain('sync temporarily unavailable')
  })

  it('shows a short partial result when some ETFs remain missing', async () => {
    vi.mocked(watchlistSyncDaily).mockResolvedValue({
      ...completeResult,
      remaining: ['ETF_C.SH', 'ETF_D.SH', 'ETF_E.SH', 'ETF_F.SH'],
    })
    const onRefetch = vi.fn()

    await render([{ symbol: 'ETF_A.SH', asset_type: 'etf', close: null }], true, onRefetch)
    await settle()

    expect(host.querySelector('[data-testid="error"]')?.textContent).toBe(
      '仍有 ETF 日线缺失：ETF_C.SH、ETF_D.SH、ETF_E.SH 等 4 只',
    )
    expect(onRefetch).toHaveBeenCalledOnce()
  })
})
