// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Indices } from './Indices'

type MinuteResponse = {
  symbol: string
  date: string
  rows: { datetime: string; open: null; high: null; low: null; close: number; volume: null; amount: null }[]
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
  mounts: 0,
}))

vi.mock('@/lib/api', () => ({
  api: {
    indexQuotes: async () => ({ rows: [] }),
    indexDaily: async (symbol: string) => ({
      symbol,
      rows: ['2026-09-09', '2026-09-10', '2026-09-11'].map((date, index) => ({
        date, open: 7 + index, high: 12, low: 7, close: 8 + index, volume: 1,
      })),
    }),
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
  fixtures.pending.length = 0
  fixtures.calls.length = 0
  fixtures.mounts = 0
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
})

// React Query batches observer notifications on the next timer tick.
async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

async function resolveDay(day: string, close: number) {
  const index = fixtures.pending.findIndex(call => call.day === day)
  expect(index).toBeGreaterThanOrEqual(0)
  const [call] = fixtures.pending.splice(index, 1)
  await act(async () => call.resolve({
    symbol: call.symbol,
    date: day,
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
    symbol: call.symbol, date: day, provider: 'txquote', data_kind: 'price_points', history_days: 5, rows: [],
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
  expect(host.textContent).toContain('来源 腾讯免费分时')
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
