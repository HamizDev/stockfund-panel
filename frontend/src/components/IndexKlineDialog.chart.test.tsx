// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { IndexKlineDialog } from './IndexKlineDialog'

const query = vi.hoisted(() => ({
  result: { data: null as any, isLoading: false, error: null as unknown },
  options: null as any,
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: (options: unknown) => {
    query.options = options
    return query.result
  },
}))
vi.mock('@/components/Modal', () => ({ Modal: ({ children }: { children: unknown }) => <div>{children as any}</div> }))
vi.mock('@/components/CandlestickChart', () => ({
  CandlestickChart: (props: Record<string, unknown>) => (
    <output data-testid="index-chart-props">{JSON.stringify(props)}</output>
  ),
}))
vi.mock('lucide-react', () => ({ X: () => null }))
vi.mock('@/lib/api', () => ({ api: { industryKline: vi.fn() } }))

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  query.options = null
  query.result = {
    data: { kline: [{ date_ms: Date.UTC(2026, 9, 16), open_price: 10, high_price: 12, low_price: 9, close_price: 11, volume: null }] },
    isLoading: false,
    error: null,
  }
  host = document.createElement('div')
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
})

it('passes the active index identity to the Canvas chart viewport context', async () => {
  await act(async () => root.render(
    <IndexKlineDialog code="000300.SH" name="沪深300" onClose={() => {}} />,
  ))

  expect(query.options).toMatchObject({ queryKey: ['industry-kline', '000300.SH'] })
  const output = host.querySelector('[data-testid="index-chart-props"]')
  expect(output).not.toBeNull()
  expect(JSON.parse(output!.textContent ?? '{}')).toEqual({
    data: [{ date: '2026-10-16', open: 10, high: 12, low: 9, close: 11, volume: null }],
    height: 300,
    symbol: '000300.SH',
    title: '沪深300指数走势',
    contextKey: '000300.SH:沪深300',
  })
})
