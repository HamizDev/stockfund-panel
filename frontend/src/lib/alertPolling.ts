import type { QuoteStreamStatus } from './useQuoteStream'

export const ALERTS_TOTAL_CONNECTED_POLL_MS = 60_000
export const ALERTS_TOTAL_FALLBACK_POLL_MS = 15_000

/** Keep a slow reconciliation poll while SSE is healthy and a faster fallback otherwise. */
export function alertsTotalPollingOptions(streamStatus: QuoteStreamStatus) {
  return {
    refetchInterval: streamStatus === 'connected'
      ? ALERTS_TOTAL_CONNECTED_POLL_MS
      : ALERTS_TOTAL_FALLBACK_POLL_MS,
    refetchIntervalInBackground: false as const,
  }
}
