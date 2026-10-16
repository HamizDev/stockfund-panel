import { describe, expect, it } from 'vitest'
import { QueryClient, QueryObserver } from '@tanstack/react-query'
import { scheduleNeighborPrefetch } from './neighborPrefetch.ts'

const tick = () => new Promise(resolve => setTimeout(resolve, 10))
function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>(done => { resolve = done })
  return { promise, resolve }
}

describe('scheduleNeighborPrefetch', () => {
  it('waits for foreground requests, then runs neighbors serially', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    const foreground = deferred()
    const neighbor = deferred()
    const calls: string[] = []
    const observer = new QueryObserver(client, {
      queryKey: ['kline-minute', 'current'],
      queryFn: async () => { await foreground.promise; return [] },
    })
    const unsubscribe = observer.subscribe(() => {})
    const stop = scheduleNeighborPrefetch(client, 'current', [
      async () => { calls.push('first'); await neighbor.promise },
      async () => { calls.push('second') },
    ])
    try {
      await tick()
      expect(calls).toEqual([])
      foreground.resolve()
      await tick()
      expect(calls).toEqual(['first'])
      neighbor.resolve()
      await tick()
      expect(calls).toEqual(['first', 'second'])
    } finally { stop(); unsubscribe(); client.clear() }
  })

  it('pauses for new foreground work and resumes after a neighbor failure', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    const neighbor = deferred()
    const foreground = deferred()
    const calls: string[] = []
    const stop = scheduleNeighborPrefetch(client, 'current', [
      async () => { calls.push('first'); await neighbor.promise },
      async () => { calls.push('second'); throw new Error('prefetch failed') },
      async () => { calls.push('third') },
    ])
    await tick()
    const observer = new QueryObserver(client, {
      queryKey: ['financials', 'metrics', 'current'],
      queryFn: async () => { await foreground.promise; throw new Error('unavailable') },
    })
    const unsubscribe = observer.subscribe(() => {})
    try {
      neighbor.resolve()
      await tick()
      expect(calls).toEqual(['first'])
      foreground.resolve()
      await tick(); await tick()
      expect(calls).toEqual(['first', 'second', 'third'])
    } finally { stop(); unsubscribe(); client.clear() }
  })

  it('stops pending and chained work when the context changes or closes', async () => {
    const client = new QueryClient()
    const neighbor = deferred()
    const calls: string[] = []
    const stop = scheduleNeighborPrefetch(client, 'current', [
      async () => { calls.push('first'); await neighbor.promise },
      async () => { calls.push('second') },
    ])
    await tick()
    stop()
    neighbor.resolve()
    await tick()
    expect(calls).toEqual(['first'])
    const stopBeforeStart = scheduleNeighborPrefetch(client, 'next', [
      async () => { calls.push('obsolete') },
    ])
    stopBeforeStart()
    await tick()
    expect(calls).toEqual(['first'])
    client.clear()
  })

  it('reuses fresh cache data without another request', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity } } })
    const key = ['kline', 'neighbor']
    client.setQueryData(key, { rows: [] })
    let requests = 0
    let completed = false
    const stop = scheduleNeighborPrefetch(client, 'current', [
      () => client.prefetchQuery({
        queryKey: key,
        staleTime: 30_000,
        queryFn: async () => { requests++; return { rows: [] } },
      }),
      async () => { completed = true },
    ])
    try {
      await tick(); await tick()
      expect(completed).toBe(true)
      expect(requests).toBe(0)
    } finally { stop(); client.clear() }
  })
})
