// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Indices } from './Indices'
import { QK } from '@/lib/queryKeys'
import { INSTRUMENT_VIEW_GC_MS } from '@/lib/useInstrumentViewCache'

type MinuteResponse = {
  symbol: string
  date: string
  rows: { datetime: string; open: null; high: null; low: null; close: number; volume: null; amount: null }[]
  source: 'live' | 'none'
  provider: string
  data_kind: 'price_points'
  history_days: number
}
const fixtures = vi.hoisted(() => ({
  pending: [] as {
    symbol: string
    day: string | undefined
    resolve: (value: MinuteResponse) => void
    reject: (reason: Error) => void
  }[],
  calls: [] as { symbol: string; day: string | undefined }[],
  dailyCalls: [] as { symbol: string; start: string; end: string }[],
  mounts: 0,
  dailyRows: [] as { date: string; open: number; high: number; low: number; close: number; volume: number }[],
}))

vi.mock('@/lib/api', () => ({
  api: {
    indexQuotes: async () => ({ rows: [] }),
    indexDaily: async (symbol: string, _days: number, range: { start: string; end: string }) => {
      fixtures.dailyCalls.push({ symbol, start: range.start, end: range.end })
      return { symbol, rows: fixtures.dailyRows.filter(row => row.date >= range.start && row.date <= range.end) }
    },
    indexMinute: (symbol: string, day?: string) => {
      fixtures.calls.push({ symbol, day })
      return new Promise<MinuteResponse>((resolve, reject) => {
        fixtures.pending.push({ symbol, day, resolve, reject })
      })
    },
  },
}))
vi.mock('@/lib/useSharedQueries', () => ({
  useCapabilities: () => ({ data: { capabilities: {} } }),
  useIndexQuotes: () => ({ data: { rows: [], count: 5 } }),
}))
vi.mock('@/components/EChartsCandlestick', () => ({
  EChartsCandlestick: ({ data, onDateClick }: {
    data: { date: string }[]; onDateClick: (date: string) => void
  }) => <div>{data.map(row => (
    <button key={row.date} data-day={row.date} onClick={() => onDateClick(row.date)}>{row.date}</button>
  ))}</div>,
}))
vi.mock('@/components/EChartsIntraday', () => ({
  EChartsIntraday: ({ data, date, prevClose, pricePointsOnly }: {
    data: MinuteResponse['rows']; date: string; prevClose?: number; pricePointsOnly?: boolean
  }) => {
    const [mount] = useState(() => ++fixtures.mounts)
    return <div data-chart data-mount={mount} data-points-only={String(pricePointsOnly)} data-prev-close={prevClose ?? ''}>{`${date}|${data[0].datetime}|${data[0].close}`}</div>
  },
}))

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  window.sessionStorage.clear()
  window.localStorage.clear()
  fixtures.pending.length = 0
  fixtures.calls.length = 0
  fixtures.dailyCalls.length = 0
  fixtures.mounts = 0
  fixtures.dailyRows = ['2026-09-09', '2026-09-10', '2026-09-11'].map((date, index) => ({
    date, open: 7 + index, high: 12, low: 7, close: 8 + index, volume: 1,
  }))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: {
    retry: false, staleTime: Infinity, gcTime: Infinity,
  } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
  vi.useRealTimers()
})

// React Query batches observer notifications on the next timer tick.
async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(0)
      else await new Promise(resolve => setTimeout(resolve, 0))
    })
  }
}

async function resolveDay(day: string, close: number) {
  const index = fixtures.pending.findIndex(call => call.day === day)
  expect(index).toBeGreaterThanOrEqual(0)
  const [call] = fixtures.pending.splice(index, 1)
  await act(async () => call.resolve({
    symbol: call.symbol,
    date: day,
    source: 'live',
    provider: 'txquote',
    data_kind: 'price_points',
    history_days: 5,
    rows: [{ datetime: `${day} 09:35:00`, open: null, high: null, low: null, close, volume: null, amount: null }],
  }))
  await settle()
}

async function resolveEmptyDay(day: string) {
  const index = fixtures.pending.findIndex(call => call.day === day)
  expect(index).toBeGreaterThanOrEqual(0)
  const [call] = fixtures.pending.splice(index, 1)
  await act(async () => call.resolve({
    symbol: call.symbol, date: day, source: 'none', provider: 'txquote', data_kind: 'price_points', history_days: 5, rows: [],
  }))
  await settle()
}

async function rejectDay(day: string, message: string) {
  const index = fixtures.pending.findIndex(call => call.day === day)
  expect(index).toBeGreaterThanOrEqual(0)
  const [call] = fixtures.pending.splice(index, 1)
  await act(async () => call.reject(new Error(message)))
  await settle()
}

async function clickDay(day: string) {
  const button = host.querySelector<HTMLButtonElement>(`[data-day="${day}"]`)
  expect(button).not.toBeNull()
  await act(async () => button!.click())
  await settle()
}

async function replaceDailyRows(dates: string[]) {
  const rows = dates.map((date, index) => ({
    date, open: 7 + index, high: 12, low: 7, close: 8 + index, volume: 1,
  }))
  fixtures.dailyRows = rows
  const query = client.getQueryCache().getAll().find(item => item.queryKey[0] === 'index-daily')
  expect(query).toBeDefined()
  await act(async () => client.setQueryData(query!.queryKey, { symbol: '000001.SH', rows }))
  await settle()
}

it('loads index points without the minute-batch capability and isolates exact dates across delayed, out-of-order and cached responses', async () => {
  await act(async () => root.render(
    <MemoryRouter><QueryClientProvider client={client}><Indices /></QueryClientProvider></MemoryRouter>,
  ))
  await settle()
  const chart = () => host.querySelector<HTMLElement>('[data-chart]')
  expect(fixtures.calls).toContainEqual({ symbol: '000001.SH', day: '2026-09-11' })
  await resolveDay('2026-09-11', 11)
  expect(chart()?.textContent).toBe('2026-09-11|2026-09-11 09:35:00|11')
  expect(chart()?.dataset.pointsOnly).toBe('true')
  expect(chart()?.dataset.prevClose).toBe('9')

  await clickDay('2026-09-10')
  expect(chart()).toBeNull()
  await clickDay('2026-09-09')
  await resolveDay('2026-09-10', 10)
  expect(chart()).toBeNull()
  await resolveDay('2026-09-09', 9)
  expect(chart()?.dataset.prevClose).toBe('')
  await clickDay('2026-09-11')
  expect(chart()?.dataset.prevClose).toBe('9')

  await clickDay('2026-09-10')
  expect(chart()?.textContent).toBe('2026-09-10|2026-09-10 09:35:00|10')
  expect(chart()?.dataset.prevClose).toBe('8')

  const mount10 = chart()?.dataset.mount
  await clickDay('2026-09-11')
  expect(chart()?.textContent).toBe('2026-09-11|2026-09-11 09:35:00|11')
  expect(chart()?.dataset.mount).not.toBe(mount10)

  const mount11 = chart()?.dataset.mount
  await act(async () => { void client.invalidateQueries({ queryKey: ['index-minute'] }) })
  await settle()
  expect(chart()?.textContent).toBe('2026-09-11|2026-09-11 09:35:00|11')
  await resolveDay('2026-09-11', 12)
  expect(chart()?.textContent).toBe('2026-09-11|2026-09-11 09:35:00|12')
  expect(chart()?.dataset.mount).toBe(mount11)
})

it('shows a distinct empty-date state with the response source and history range', async () => {
  await act(async () => root.render(
    <MemoryRouter><QueryClientProvider client={client}><Indices /></QueryClientProvider></MemoryRouter>,
  ))
  await settle()
  await resolveEmptyDay('2026-09-11')

  expect(host.textContent).toContain('日期 2026-09-11')
  expect(host.textContent).toContain('价格点位')
  expect(host.textContent).toContain('来源 暂无有效数据 · 腾讯免费分时')
  expect(host.textContent).toContain('近5个交易日，以来源实际覆盖为准')
  expect(host.textContent).toContain('2026-09-11 暂无指数分时数据')
  expect(host.querySelector('[data-chart]')).toBeNull()
  expect(Array.from(host.querySelectorAll('button')).some(button => button.textContent === '重试')).toBe(false)
})

it('keeps source failure separate from empty dates and retries the same requested date', async () => {
  await act(async () => root.render(
    <MemoryRouter><QueryClientProvider client={client}><Indices /></QueryClientProvider></MemoryRouter>,
  ))
  await settle()
  expect(fixtures.calls).toContainEqual({ symbol: '000001.SH', day: '2026-09-11' })
  await rejectDay('2026-09-11', 'source unavailable')

  expect(host.textContent).toContain('2026-09-11 指数分时加载失败')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('重试')
  expect(host.textContent).not.toContain('暂无指数分时数据')

  const retry = Array.from(host.querySelectorAll('button')).find(button => button.textContent === '重试')
  expect(retry).toBeDefined()
  await act(async () => retry!.click())
  await settle()
  expect(fixtures.calls.filter(call => call.day === '2026-09-11')).toHaveLength(2)
  await resolveDay('2026-09-11', 11.5)
  expect(host.querySelector('[data-chart]')?.textContent).toBe('2026-09-11|2026-09-11 09:35:00|11.5')
})

it('follows a newly available daily date, then keeps a manually selected historical date pinned', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-15T02:00:00.000Z')) // Tuesday 10:00 Beijing
  fixtures.dailyRows = [{ date: '2026-09-14', open: 7, high: 12, low: 7, close: 8, volume: 1 }]

  await act(async () => root.render(
    <MemoryRouter><QueryClientProvider client={client}><Indices /></QueryClientProvider></MemoryRouter>,
  ))
  await settle()
  expect(fixtures.calls).toContainEqual({ symbol: '000001.SH', day: '2026-09-14' })
  await resolveDay('2026-09-14', 14)
  expect(host.textContent).toContain('日期 2026-09-14 · 跟随最新')

  await replaceDailyRows(['2026-09-14', '2026-09-15'])
  expect(fixtures.calls).toContainEqual({ symbol: '000001.SH', day: '2026-09-15' })
  await resolveDay('2026-09-15', 15)
  expect(host.textContent).toContain('日期 2026-09-15 · 跟随最新')

  await clickDay('2026-09-14')
  expect(host.textContent).toContain('日期 2026-09-14 · 手选历史')
  await replaceDailyRows(['2026-09-14', '2026-09-15', '2026-09-16'])
  expect(host.textContent).toContain('日期 2026-09-14 · 手选历史')
  expect(fixtures.calls.some(call => call.day === '2026-09-16')).toBe(false)
})

it('advances a saved follow-latest end date at the next Beijing session and fetches that date', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-14T02:00:00.000Z')) // Monday 10:00 Beijing
  fixtures.dailyRows = [{ date: '2026-09-14', open: 7, high: 12, low: 7, close: 8, volume: 1 }]
  window.sessionStorage.setItem('page-view:indices', JSON.stringify({
    selected: '000001.SH',
    range: { start: '2026-09-09', end: '2026-09-14' },
    selectedDate: null,
    followLatest: true,
    rangeEndFollowsToday: true,
  }))

  await act(async () => root.render(
    <MemoryRouter><QueryClientProvider client={client}><Indices /></QueryClientProvider></MemoryRouter>,
  ))
  await settle()
  expect(fixtures.dailyCalls).toContainEqual({ symbol: '000001.SH', start: '2026-09-09', end: '2026-09-14' })
  await resolveDay('2026-09-14', 14)

  fixtures.dailyRows = [
    { date: '2026-09-14', open: 7, high: 12, low: 7, close: 8, volume: 1 },
    { date: '2026-09-15', open: 8, high: 13, low: 8, close: 9, volume: 1 },
  ]
  vi.setSystemTime(new Date('2026-09-15T01:30:00.000Z')) // Tuesday 09:30 Beijing
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
  await settle()

  expect(fixtures.dailyCalls).toContainEqual({ symbol: '000001.SH', start: '2026-09-09', end: '2026-09-15' })
  expect(fixtures.calls).toContainEqual({ symbol: '000001.SH', day: '2026-09-15' })
  await resolveDay('2026-09-15', 15)
  expect(host.textContent).toContain('日期 2026-09-15 · 跟随最新')
})

it('keeps a saved historical range and selected minute date fixed across the Beijing date change', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-14T02:00:00.000Z')) // Monday 10:00 Beijing
  fixtures.dailyRows = [{ date: '2026-09-14', open: 7, high: 12, low: 7, close: 8, volume: 1 }]
  window.sessionStorage.setItem('page-view:indices', JSON.stringify({
    selected: '000001.SH',
    range: { start: '2026-09-09', end: '2026-09-14' },
    selectedDate: '2026-09-14',
    followLatest: false,
    rangeEndFollowsToday: false,
  }))

  await act(async () => root.render(
    <MemoryRouter><QueryClientProvider client={client}><Indices /></QueryClientProvider></MemoryRouter>,
  ))
  await settle()
  await resolveDay('2026-09-14', 14)
  const callsBeforeDateChange = fixtures.dailyCalls.length
  expect(host.querySelectorAll<HTMLInputElement>('input[type="date"]')[1].value).toBe('2026-09-14')

  vi.setSystemTime(new Date('2026-09-15T01:30:00.000Z')) // Tuesday 09:30 Beijing
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
  await settle()

  expect(fixtures.dailyCalls).toHaveLength(callsBeforeDateChange)
  expect(fixtures.dailyCalls[0].end).toBe('2026-09-14')
  expect(fixtures.calls.filter(call => call.day === '2026-09-15')).toHaveLength(0)
  expect(host.querySelectorAll<HTMLInputElement>('input[type="date"]')[1].value).toBe('2026-09-14')
  expect(host.textContent).toContain('日期 2026-09-14 · 手选历史')
})

it('refreshes current-day index minutes every 30 seconds while retaining the last valid chart', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-14T02:00:00.000Z')) // Monday 10:00 Beijing
  fixtures.dailyRows = [{ date: '2026-09-14', open: 7, high: 12, low: 7, close: 8, volume: 1 }]

  await act(async () => root.render(
    <MemoryRouter><QueryClientProvider client={client}><Indices /></QueryClientProvider></MemoryRouter>,
  ))
  await settle()
  await resolveDay('2026-09-14', 14)
  const chart = host.querySelector<HTMLElement>('[data-chart]')
  const mount = chart?.dataset.mount
  expect(chart?.textContent).toBe('2026-09-14|2026-09-14 09:35:00|14')

  await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
  expect(fixtures.calls.filter(call => call.day === '2026-09-14')).toHaveLength(2)
  expect(host.querySelector('[data-chart]')?.textContent).toBe('2026-09-14|2026-09-14 09:35:00|14')
  expect(host.querySelector<HTMLElement>('[data-chart]')?.dataset.mount).toBe(mount)

  await resolveDay('2026-09-14', 15)
  expect(host.querySelector('[data-chart]')?.textContent).toBe('2026-09-14|2026-09-14 09:35:00|15')
  expect(host.querySelector<HTMLElement>('[data-chart]')?.dataset.mount).toBe(mount)
})

it('removes the previous inactive index daily cache and reuses the selected cache after route remount', async () => {
  client.setQueryDefaults(['index-daily'], { gcTime: 0 })

  const renderIndices = () => root.render(
    <MemoryRouter><QueryClientProvider client={client}><Indices /></QueryClientProvider></MemoryRouter>,
  )
  await act(async () => renderIndices())
  await settle()

  const oldEntry = client.getQueryCache().getAll().find(query =>
    query.queryKey[0] === 'index-daily' && query.queryKey[1] === '000001.SH',
  )
  expect(oldEntry).toBeDefined()
  expect(oldEntry?.meta).toMatchObject({ instrumentView: 'indices', symbol: '000001.SH' })
  expect(oldEntry?.options.gcTime).toBe(INSTRUMENT_VIEW_GC_MS)
  const oldKey = oldEntry!.queryKey as ReturnType<typeof QK.indexDaily>
  const nextKey = QK.indexDaily('399001.SZ', oldKey[2], oldKey[3])

  const nextIndex = [...host.querySelectorAll('button')].find(button => button.textContent?.includes('399001.SZ'))
  expect(nextIndex).toBeDefined()
  await act(async () => nextIndex!.click())
  await settle()

  expect(client.getQueryCache().find({ queryKey: oldKey, exact: true })).toBeUndefined()
  expect(client.getQueryCache().find({ queryKey: nextKey, exact: true })).toBeDefined()
  const callsAfterSwitch = fixtures.dailyCalls.length

  await act(async () => root.render(null))
  await settle()
  expect(client.getQueryCache().find({ queryKey: nextKey, exact: true })).toBeDefined()

  await act(async () => renderIndices())
  await settle()
  expect(fixtures.dailyCalls).toHaveLength(callsAfterSwitch)
})
