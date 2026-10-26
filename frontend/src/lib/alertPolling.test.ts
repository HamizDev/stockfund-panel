import { describe, expect, it } from 'vitest'
import { alertsTotalPollingOptions } from './alertPolling'

describe('alerts total polling', () => {
  it('slows reconciliation while SSE is connected and pauses in background', () => {
    expect(alertsTotalPollingOptions('connected')).toEqual({
      refetchInterval: 60_000,
      refetchIntervalInBackground: false,
    })
  })

  it.each(['reconnecting', 'disconnected'] as const)(
    'keeps the fallback cadence when SSE is %s and pauses in background',
    status => {
      expect(alertsTotalPollingOptions(status)).toEqual({
        refetchInterval: 15_000,
        refetchIntervalInBackground: false,
      })
    },
  )
})
