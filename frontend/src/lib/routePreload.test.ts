import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRoutePreloader } from './routePreload'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

async function flushMicrotasks() {
  for (let turn = 0; turn < 8; turn += 1) await Promise.resolve()
}

afterEach(() => vi.useRealTimers())

describe('route preloading', () => {
  it('delays pointer intent and starts focus intent immediately', async () => {
    vi.useFakeTimers()
    const pointerLoader = vi.fn(async () => undefined)
    const focusLoader = vi.fn(async () => undefined)
    const schedule = createRoutePreloader({ '/watchlist': pointerLoader, '/screener': focusLoader })

    schedule('/watchlist')
    await vi.advanceTimersByTimeAsync(119)
    expect(pointerLoader).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    await flushMicrotasks()
    expect(pointerLoader).toHaveBeenCalledTimes(1)

    schedule('/screener', { immediate: true })
    await flushMicrotasks()
    expect(focusLoader).toHaveBeenCalledTimes(1)
  })

  it('coalesces concurrent requests for one module and remembers success', async () => {
    const load = deferred<void>()
    const loader = vi.fn(() => load.promise)
    const schedule = createRoutePreloader({ '/watchlist': loader })

    schedule('/watchlist', { immediate: true })
    schedule('/watchlist', { immediate: true })
    await flushMicrotasks()
    expect(loader).toHaveBeenCalledTimes(1)

    load.resolve()
    await flushMicrotasks()
    schedule('/watchlist', { immediate: true })
    await flushMicrotasks()
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('swallows a failed preload and lets a later intent retry', async () => {
    const loader = vi.fn()
      .mockRejectedValueOnce(new Error('temporary chunk failure'))
      .mockResolvedValueOnce(undefined)
    const schedule = createRoutePreloader({ '/watchlist': loader })

    schedule('/watchlist', { immediate: true })
    await flushMicrotasks()
    await flushMicrotasks()
    expect(loader).toHaveBeenCalledTimes(1)

    schedule('/watchlist', { immediate: true })
    await flushMicrotasks()
    await flushMicrotasks()
    expect(loader).toHaveBeenCalledTimes(2)
  })

  it('ignores unknown and external routes', async () => {
    const loader = vi.fn(async () => undefined)
    const schedule = createRoutePreloader({ '/watchlist': loader })

    schedule('/not-a-route', { immediate: true })
    schedule('https://outside.example/watchlist', { immediate: true })
    await flushMicrotasks()

    expect(loader).not.toHaveBeenCalled()
  })

  it('caps active imports at two and cancels a pointer route still queued', async () => {
    vi.useFakeTimers()
    const gates = new Map([
      ['/watchlist', deferred<void>()],
      ['/screener', deferred<void>()],
      ['/factors', deferred<void>()],
      ['/monitor', deferred<void>()],
    ])
    const started: string[] = []
    let active = 0
    let maxActive = 0
    const loader = (path: string) => vi.fn(() => {
      started.push(path)
      active += 1
      maxActive = Math.max(maxActive, active)
      return gates.get(path)!.promise.finally(() => { active -= 1 })
    })
    const schedule = createRoutePreloader({
      '/watchlist': loader('/watchlist'),
      '/screener': loader('/screener'),
      '/factors': loader('/factors'),
      '/monitor': loader('/monitor'),
    })

    schedule('/watchlist', { immediate: true })
    schedule('/screener', { immediate: true })
    const cancelFactors = schedule('/factors')
    schedule('/monitor', { immediate: true })
    await flushMicrotasks()
    expect(started).toEqual(['/watchlist', '/screener'])

    await vi.advanceTimersByTimeAsync(120)
    cancelFactors()
    gates.get('/watchlist')!.resolve()
    await flushMicrotasks()
    expect(started).toEqual(['/watchlist', '/screener', '/monitor'])
    expect(maxActive).toBe(2)

    gates.get('/screener')!.resolve()
    gates.get('/monitor')!.resolve()
    await flushMicrotasks()
    expect(started).not.toContain('/factors')
  })
})
