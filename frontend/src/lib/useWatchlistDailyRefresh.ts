import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

export interface WatchlistDailyRefreshRow {
  symbol: string
  asset_type: string
  close: number | null
}

export interface WatchlistDailySyncResult {
  requested: string[]
  rows_written: number
  remaining: string[]
  as_of: string | null
  job_id?: string
  warnings?: string[]
}

function hasUsableClose(value: number | null): boolean {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

function errorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.message.trim()) return reason.message
  if (typeof reason === 'string' && reason.trim()) return reason
  return 'ETF 日线刷新失败，请稍后重试'
}

function partialMessage(symbols: string[]): string {
  const preview = symbols.slice(0, 3).join('、')
  const more = symbols.length > 3 ? ` 等 ${symbols.length} 只` : ''
  return `仍有 ETF 日线缺失：${preview}${more}`
}

export function useWatchlistDailyRefresh(
  rows: readonly WatchlistDailyRefreshRow[],
  ready: boolean,
  onRefetch: () => void | Promise<void>,
): { isPending: boolean; error: string | null; refresh: () => void } {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const onRefetchRef = useRef(onRefetch)
  const attemptedAutoSyncsRef = useRef(new Set<string>())
  const inFlightRef = useRef(false)
  onRefetchRef.current = onRefetch

  const etfSymbols = [...new Set(rows
    .filter(row => row.asset_type === 'etf')
    .map(row => row.symbol))].sort()
  const missingEtfSymbols = [...new Set(rows
    .filter(row => row.asset_type === 'etf' && !hasUsableClose(row.close))
    .map(row => row.symbol))].sort()
  const missingKey = JSON.stringify(missingEtfSymbols)

  const mutation = useMutation<WatchlistDailySyncResult, Error, boolean>({
    mutationFn: (force) => api.watchlistSyncDaily(force),
    onSettled: async (result, mutationError) => {
      let finalError = mutationError ? errorMessage(mutationError) : null
      try {
        const invalidations = await Promise.allSettled([
          queryClient.invalidateQueries({ queryKey: ['watchlist-enriched'] }),
          queryClient.invalidateQueries({ queryKey: ['kline-batch'] }),
        ])
        if (!finalError && invalidations.some(item => item.status === 'rejected')) {
          finalError = 'ETF 日线已提交，但列表缓存刷新失败'
        }

        try {
          await onRefetchRef.current()
        } catch (refetchError) {
          if (!finalError) finalError = errorMessage(refetchError)
        }

        if (!finalError && result?.remaining.length) {
          finalError = partialMessage(result.remaining)
        }
        if (!finalError && result?.warnings?.length) {
          finalError = result.warnings.join(' ')
        }
        setError(finalError)
      } finally {
        inFlightRef.current = false
      }
    },
  })

  const requestSync = useCallback((force: boolean) => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    setError(null)
    mutation.mutate(force)
  }, [mutation.mutate])

  useEffect(() => {
    if (!ready || missingEtfSymbols.length === 0 || mutation.isPending) return
    if (attemptedAutoSyncsRef.current.has(missingKey)) return

    attemptedAutoSyncsRef.current.add(missingKey)
    requestSync(false)
  }, [ready, missingKey, missingEtfSymbols.length, mutation.isPending, requestSync])

  const refresh = useCallback(() => {
    if (inFlightRef.current) return

    if (etfSymbols.length === 0) {
      setError(null)
      void Promise.resolve()
        .then(() => onRefetchRef.current())
        .catch(refetchError => setError(errorMessage(refetchError)))
      return
    }

    // A manual forced sync satisfies this missing-set attempt; do not follow it
    // with an automatic non-forced request when the mutation settles.
    if (missingEtfSymbols.length) attemptedAutoSyncsRef.current.add(missingKey)
    requestSync(true)
  }, [etfSymbols.length, missingEtfSymbols.length, missingKey, requestSync])

  return { isPending: mutation.isPending, error, refresh }
}
