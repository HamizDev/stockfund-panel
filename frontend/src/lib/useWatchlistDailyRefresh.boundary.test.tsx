// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useWatchlistDailyRefresh, type WatchlistDailyRefreshRow, type WatchlistDailySyncResult } from './useWatchlistDailyRefresh'

const { watchlistSyncDaily } = vi.hoisted(() => ({ watchlistSyncDaily: vi.fn() }))
vi.mock('./api', () => ({ api: { watchlistSyncDaily } }))

const completeResult: WatchlistDailySyncResult = {
  requested: ['ETF_A.SH'], rows_written: 1, remaining: [], as_of: '2026-10-23',
}

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

function HookProbe({ rows }: { rows: readonly WatchlistDailyRefreshRow[] }) {
  useWatchlistDailyRefresh(rows, true, vi.fn())
  return <div />
}

async function render(rows: readonly WatchlistDailyRefreshRow[]) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <HookProbe rows={rows} />
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

describe('useWatchlistDailyRefresh boundary checks', () => {
  it('treats nonpositive and nonfinite ETF closes as missing', async () => {
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
})