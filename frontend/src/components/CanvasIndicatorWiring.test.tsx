// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { TradingViewDailyChart } from './TradingViewDailyChart'
import { CryptoMarketChart } from '@/custom/crypto_paper/CryptoMarketChart'

vi.mock('@/components/CanvasMarketChart', () => ({
  CanvasMarketChart: (props: Record<string, unknown>) => <output data-testid="canvas-props">{JSON.stringify(props)}</output>,
}))

let root: Root
let host: HTMLDivElement

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

it('wires DC20 as default-off research lines for stock and crypto Canvas charts', async () => {
  await act(async () => root.render(<>
    <TradingViewDailyChart
      data={[{ date: '2026-10-16', open: 10, high: 12, low: 9, close: 11, volume: null }]}
      symbol="600000.SH" contextKey="stock" showControls={false}
    />
    <CryptoMarketChart
      bars={[{
        open_time_ms: 60_000, close_time_ms: 119_999, open: '10', high: '12', low: '9', close: '11', volume: '1',
      }]}
      symbol="BTCUSDT" interval="1m" market="spot"
    />
  </>))

  const props = [...host.querySelectorAll('[data-testid="canvas-props"]')]
    .map(output => JSON.parse(output.textContent ?? '{}') as {
      bars: Array<{ volume: number | null }>
      series: Array<{ id: string; visibleByDefault?: boolean; values: Array<number | null> }>
    })
  expect(props).toHaveLength(2)
  expect(props[0].bars[0].volume).toBeNull()
  expect(props[1].bars[0].volume).toBe(1)
  for (const chart of props) {
    expect(chart.series.find(item => item.id === 'dc20-upper')).toMatchObject({ visibleByDefault: false, values: [null] })
    expect(chart.series.find(item => item.id === 'dc20-lower')).toMatchObject({ visibleByDefault: false, values: [null] })
  }
})
