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

it('briefly retries a missing current-session reference during lunch without polling forever', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-22T04:00:00.000Z')) // Thursday 12:00 Beijing
  const refresh = minuteRefetchInterval(30_000, { retryMissingReference: true })
  const query = {
    queryKey: QK.analysisMinute('515880.SH', '', 'stock'),
    state: {
      dataUpdatedAt: 1,
      data: { date: '2026-10-22', source: 'live', prev_close: null, prev_close_source: 'none',
        rows: [{ datetime: '2026-10-22 11:30:00' }] },
    },
  }
  for (const updatedAt of [1, 2, 3]) {
    query.state.dataUpdatedAt = updatedAt
    expect(refresh(query)).toBe(30_000)
    expect(refresh(query)).toBe(30_000) // callback re-evaluation is not another fetch
  }
  query.state.dataUpdatedAt = 4
  expect(refresh(query)).toBe(60 * 60 * 1_000)
})

it('stops reference recovery on success and keeps historical and overnight queries dormant', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-22T04:00:00.000Z'))
  const refresh = minuteRefetchInterval(30_000, { retryMissingReference: true })
  const query = {
    queryKey: QK.analysisMinute('515880.SH', '', 'stock'),
    state: { dataUpdatedAt: 1, data: {
      date: '2026-10-22', source: 'live', prev_close: null as number | null, prev_close_source: 'none',
      rows: [{ datetime: '2026-10-22 11:30:00' }],
    } },
  }
  expect(refresh(query)).toBe(30_000)
  query.state.dataUpdatedAt = 2
  query.state.data.prev_close = .535
  query.state.data.prev_close_source = 'online_daily'
  expect(refresh(query)).toBe(60 * 60 * 1_000)
  query.state.data.prev_close = null
  query.state.data.prev_close_source = 'none'
  query.state.dataUpdatedAt = 3
  expect(refresh(query)).toBe(30_000)
  expect(refresh({ ...query, queryKey: QK.analysisMinute('515880.SH', '2026-10-21', 'stock') })).toBe(false)
  vi.setSystemTime(new Date('2026-10-22T08:00:00.000Z')) // Thursday 16:00 Beijing
  query.state.data.rows = [{ datetime: '2026-10-22 15:00:00' }]
  expect(refresh(query)).toBe(17.5 * 60 * 60 * 1_000)
})

it('counts failed recovery requests toward the lunch retry limit', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-22T04:00:00.000Z'))
  const refresh = minuteRefetchInterval(30_000, { retryMissingReference: true })
  const query = {
    queryKey: QK.analysisMinute('515880.SH', '', 'stock'),
    state: { dataUpdatedAt: 1, errorUpdatedAt: 0, data: {
      date: '2026-10-22', prev_close: null, prev_close_source: 'none',
      rows: [{ datetime: '2026-10-22 11:30:00' }],
    } },
  }
  expect(refresh(query)).toBe(30_000)
  for (const failedAt of [1, 2]) {
    query.state.errorUpdatedAt = failedAt
    expect(refresh(query)).toBe(30_000)
  }
  query.state.errorUpdatedAt = 3
  expect(refresh(query)).toBe(60 * 60 * 1_000)
})
