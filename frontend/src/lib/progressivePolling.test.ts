import { afterEach, expect, it, vi } from 'vitest'
import { progressiveLoadingInterval } from './progressivePolling'

afterEach(() => vi.useRealTimers())

it('backs off by elapsed time without counting callback invocations', () => {
  vi.useFakeTimers()
  const query = {}

  expect(progressiveLoadingInterval(query, true, '0/20', 60_000)).toBe(2_000)
  for (let i = 0; i < 25; i += 1) {
    expect(progressiveLoadingInterval(query, true, '0/20', 60_000)).toBe(2_000)
  }

  vi.advanceTimersByTime(14_999)
  expect(progressiveLoadingInterval(query, true, '0/20', 60_000)).toBe(2_000)
  vi.advanceTimersByTime(1)
  expect(progressiveLoadingInterval(query, true, '0/20', 60_000)).toBe(5_000)

  vi.advanceTimersByTime(44_999)
  expect(progressiveLoadingInterval(query, true, '0/20', 60_000)).toBe(5_000)
  vi.advanceTimersByTime(1)
  expect(progressiveLoadingInterval(query, true, '0/20', 60_000)).toBe(15_000)

  vi.advanceTimersByTime(59_999)
  expect(progressiveLoadingInterval(query, true, '0/20', 60_000)).toBe(15_000)
  vi.advanceTimersByTime(1)
  expect(progressiveLoadingInterval(query, true, '0/20', 60_000)).toBe(30_000)
})

it('resets to two seconds when reported progress changes', () => {
  vi.useFakeTimers()
  const query = {}

  expect(progressiveLoadingInterval(query, true, '1/20', 60_000)).toBe(2_000)
  vi.advanceTimersByTime(60_000)
  expect(progressiveLoadingInterval(query, true, '1/20', 60_000)).toBe(15_000)

  expect(progressiveLoadingInterval(query, true, '2/20', 60_000)).toBe(2_000)
  vi.advanceTimersByTime(14_999)
  expect(progressiveLoadingInterval(query, true, '2/20', 60_000)).toBe(2_000)
  vi.advanceTimersByTime(1)
  expect(progressiveLoadingInterval(query, true, '2/20', 60_000)).toBe(5_000)
})

it('isolates query progress and restores the steady interval after loading finishes', () => {
  vi.useFakeTimers()
  const hkQuery = {}
  const usQuery = {}

  expect(progressiveLoadingInterval(hkQuery, true, '0/20', 60_000)).toBe(2_000)
  vi.advanceTimersByTime(60_000)
  expect(progressiveLoadingInterval(hkQuery, true, '0/20', 60_000)).toBe(15_000)

  expect(progressiveLoadingInterval(usQuery, true, '0/20', 60_000)).toBe(2_000)
  expect(progressiveLoadingInterval(hkQuery, false, '20/20', 60_000)).toBe(60_000)
  expect(progressiveLoadingInterval(hkQuery, true, '20/20', 60_000)).toBe(2_000)
})
