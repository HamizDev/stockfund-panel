// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CandlestickChart, fmtBigNum } from './CandlestickChart'

vi.mock('@/components/CanvasMarketChart', () => ({
  CanvasMarketChart: (props: Record<string, any>) => (
    <output data-testid="canvas-props" data-formatted-price={props.formatPrice(12.345)}>{JSON.stringify(props)}</output>
  ),
}))

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  vi.unstubAllGlobals()
})

it('maps index OHLC rows into Canvas bars without inventing missing volume', async () => {
  await act(async () => root.render(
    <CandlestickChart
      data={[
        { date: '2026-10-15', open: 10, high: 12, low: 9, close: 11 },
        { date: '2026-10-16', open: 11, high: 13, low: 10, close: 12, volume: 0 },
      ]}
      symbol="000300.SH"
      contextKey="000300.SH:沪深300"
      title="沪深300指数走势"
      height={300}
    />,
  ))

  const output = host.querySelector('[data-testid="canvas-props"]')
  expect(output).not.toBeNull()
  const props = JSON.parse(output!.textContent ?? '{}') as {
    bars: Array<{ time: string; volume: number | null }>
    symbol: string
    contextKey: string
    title: string
    height: number
    showControls: boolean
    upColor: string
    downColor: string
  }
  expect(props).toMatchObject({
    symbol: '000300.SH', contextKey: '000300.SH:沪深300', title: '沪深300指数走势',
    height: 300, showControls: false, upColor: '#F04438', downColor: '#12B76A',
  })
  expect(props.bars).toEqual([
    { time: '2026-10-15', open: 10, high: 12, low: 9, close: 11, volume: null },
    { time: '2026-10-16', open: 11, high: 13, low: 10, close: 12, volume: 0 },
  ])
  expect(output?.getAttribute('data-formatted-price')).toBe('12.35')
})

it('keeps the compact large-number formatter used by the index chart API', () => {
  expect(fmtBigNum(123)).toBe('123')
  expect(fmtBigNum(12_345)).toBe('1万')
  expect(fmtBigNum(123_000_000)).toBe('1.23亿')
  expect(fmtBigNum(1_200_000_000_000)).toBe('1.20万亿')
})
