import { afterEach, expect, it, vi } from 'vitest'
import { klineMinuteQueryOptions, minuteRefetchInterval } from './kline'
import { QK } from './queryKeys'

afterEach(() => vi.useRealTimers())

function interval(data: unknown, queryKey: readonly unknown[]): number | false {
  return minuteRefetchInterval(30_000)({ queryKey, state: { data } })
}

it('includes live mode in minute cache keys and keeps legacy keys usable as prefixes', () => {
  const legacy = QK.klineMinute('600000.SH', '2026-09-14')
  const noLive = klineMinuteQueryOptions('600000.SH', '2026-09-14')
  const cached = klineMinuteQueryOptions('600000.SH', '2026-09-14', false)
  const live = klineMinuteQueryOptions('600000.SH', '2026-09-14', true)

  expect(noLive.queryKey).toEqual(legacy)
  expect(cached.queryKey).not.toEqual(live.queryKey)
  expect(cached.queryKey.slice(0, legacy.length)).toEqual(legacy)
  expect(live.queryKey.slice(0, legacy.length)).toEqual(legacy)
})

it('keeps retrying an empty latest response during the Beijing trading session', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-14T02:00:00.000Z')) // Monday 10:00 Beijing

  expect(interval({ date: '2026-09-14', source: 'none', rows: [] }, QK.klineMinute('600000.SH', '', true)))
    .toBe(30_000)
})

it('wakes a latest query with an old date at the next Beijing weekday open', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-13T02:00:00.000Z')) // Sunday 10:00 Beijing

  expect(interval({ date: '2026-09-11', source: 'local', rows: [] }, QK.klineMinute('600000.SH', '', true)))
    .toBe(23.5 * 60 * 60 * 1_000)
})

it('keeps checking a stale latest response during an open trading session', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-14T02:00:00.000Z')) // Monday 10:00 Beijing

  expect(interval({ date: '2026-09-11', source: 'local', rows: [] }, QK.klineMinute('600000.SH', '', true)))
    .toBe(30_000)
})

it('does not poll an explicitly requested historical date', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-14T02:00:00.000Z')) // Monday 10:00 Beijing

  expect(interval(undefined, QK.klineMinute('600000.SH', '2026-09-11', true))).toBe(false)
})

it('waits through pre-open and lunch instead of polling at the live interval', () => {
  vi.useFakeTimers()
  const latestKey = QK.klineMinute('600000.SH', '', true)
  const emptyToday = { date: '2026-09-14', source: 'none', rows: [] }

  vi.setSystemTime(new Date('2026-09-14T00:00:00.000Z')) // Monday 08:00 Beijing
  expect(interval(emptyToday, latestKey)).toBe(90 * 60 * 1_000)

  vi.setSystemTime(new Date('2026-09-14T04:00:00.000Z')) // Monday 12:00 Beijing
  expect(interval(emptyToday, latestKey)).toBe(60 * 60 * 1_000)
})

it('keeps latest queries dormant after the close but schedules the next weekday', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-14T08:00:00.000Z')) // Monday 16:00 Beijing

  expect(interval({
    date: '2026-09-14',
    source: 'live',
    rows: [{ datetime: '2026-09-14 15:00:00' }],
  }, QK.klineMinute('600000.SH', '', true))).toBe(17.5 * 60 * 60 * 1_000)
  expect(interval({ date: '2026-09-14', source: 'none', rows: [] }, QK.klineMinute('600000.SH', '2026-09-14', true)))
    .toBe(false)
})
