import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

/** One local batch lookup per stable symbol set; shared with other name queries. */
export function usePaperInstrumentNames(symbols: string[]) {
  const uniqueSymbols = [...new Set(symbols.filter(Boolean))].sort()
  return useQuery({
    queryKey: ['instrument-names', uniqueSymbols.join(',')],
    queryFn: () => api.instrumentNames(uniqueSymbols),
    enabled: uniqueSymbols.length > 0,
    staleTime: 300_000,
  })
}
