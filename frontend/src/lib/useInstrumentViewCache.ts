import { useEffect } from 'react'
import { useQueryClient, type Query } from '@tanstack/react-query'

export const INSTRUMENT_VIEW_GC_MS = 30 * 60_000

/** Retain one instrument per view without evicting queries used elsewhere. */
export function useInstrumentViewCache(scope: string, symbol: string, date?: string | null) {
  const client = useQueryClient()
  useEffect(() => {
    const filters = { predicate: (query: Query) => {
      if (query.getObserversCount() > 0 || query.meta?.instrumentView !== scope) return false
      if (query.meta.symbol !== symbol) return true
      // Minute panels keep latest plus the currently selected historical day.
      return date !== undefined && query.meta.minuteDate != null
        && query.meta.minuteDate !== '' && query.meta.minuteDate !== date
    } }
    void client.cancelQueries(filters).then(() => client.removeQueries(filters))
  }, [client, scope, symbol, date])
}
