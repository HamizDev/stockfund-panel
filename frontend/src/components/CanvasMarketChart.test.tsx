// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CanvasMarketChart } from './CanvasMarketChart'

const canvasContext = {
  setTransform: vi.fn(), clearRect: vi.fn(), fillRect: vi.fn(), strokeRect: vi.fn(),
  beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), fill: vi.fn(),
  arc: vi.fn(), closePath: vi.fn(), save: vi.fn(), restore: vi.fn(), setLineDash: vi.fn(),
  fillText: vi.fn(), measureText: vi.fn((text: string) => ({ width: text.length * 6 })),
} as unknown as CanvasRenderingContext2D

let root: Root
let host: HTMLDivElement

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(canvasContext)
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

it('renders empty input safely and exposes an empty chart status', async () => {
  await act(async () => root.render(<CanvasMarketChart bars={[]} symbol="TEST" contextKey="TEST" showControls={false} />))
  expect(host.querySelector('canvas')?.getAttribute('aria-label')).toContain('Canvas K线图')
  expect(host.querySelector('[role="status"]')?.textContent).toContain('暂无有效 K 线数据')
  expect(HTMLCanvasElement.prototype.getContext).toHaveBeenCalled()
})

it('keeps missing volume visible, delays single clicks, and routes real double-clicks only to price selection', async () => {
  const onBarClick = vi.fn()
  const onPriceDoubleClick = vi.fn()
  await act(async () => root.render(<CanvasMarketChart
    bars={[{ time: '2026-10-16', open: 10, high: 12, low: 9, close: 11, volume: null }]}
    symbol="TEST" contextKey="TEST" height={300} showControls={false}
    formatPrice={value => typeof value === 'number' ? value.toFixed(2) : '—'}
    linkedPrice={-200}
    priceLines={[
      { value: 11, label: 'a-long-price-line-label-that-expands-the-price-axis' },
      { value: -100, label: 'invalid-negative-price' },
      { value: 1000, start: '2026-10-17', end: '2026-10-18', label: 'outside-visible-date-range' },
    ]}
    onBarClick={onBarClick} onPriceDoubleClick={onPriceDoubleClick}
  />))

  const canvas = host.querySelector('canvas')!
  expect(host.textContent).toContain('成交量缺失')
  expect(canvas.width).toBeGreaterThan(1)
  expect(host.querySelector('[aria-live="polite"]')?.getAttribute('title')).toContain('成交量缺失')
  expect(host.querySelector('section > p')?.textContent).toContain('成交量缺失')
  vi.useFakeTimers()
  await act(async () => canvas.dispatchEvent(new MouseEvent('click', {
    bubbles: true, detail: 1, clientX: 550, clientY: 100,
  })))
  await act(async () => vi.advanceTimersByTime(550))
  expect(onBarClick).not.toHaveBeenCalled()
  await act(async () => {
    canvas.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0, clientX: 292, clientY: 100 }))
  })
  expect(onBarClick).not.toHaveBeenCalled()
  await act(async () => canvas.dispatchEvent(new MouseEvent('click', {
    bubbles: true, detail: 1, clientX: 292, clientY: 100,
  })))
  expect(onBarClick).not.toHaveBeenCalled()
  await act(async () => vi.advanceTimersByTime(550))
  expect(onBarClick).toHaveBeenCalledWith('2026-10-16')

  await act(async () => {
    canvas.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 2, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, clientX: 292, clientY: 100 }))
  })
  await act(async () => vi.advanceTimersByTime(550))
  expect(onBarClick).toHaveBeenCalledTimes(1)
  expect(onPriceDoubleClick).toHaveBeenCalledTimes(1)
  expect(onPriceDoubleClick.mock.calls[0][0]).toBeGreaterThan(9)
  expect(onPriceDoubleClick.mock.calls[0][0]).toBeLessThan(12)
  expect(onPriceDoubleClick.mock.calls[0][1]).toBe(11)
})

it('does not turn pointer cancellation into a date click', async () => {
  const onBarClick = vi.fn()
  await act(async () => root.render(<CanvasMarketChart
    bars={[{ time: '2026-10-16', open: 10, high: 12, low: 9, close: 11, volume: null }]}
    symbol="TEST" contextKey="TEST" showControls={false} onBarClick={onBarClick}
  />))
  vi.useFakeTimers()
  const canvas = host.querySelector('canvas')!
  await act(async () => {
    canvas.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('pointercancel', { bubbles: true, button: 0, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1, clientX: 292, clientY: 100 }))
    vi.advanceTimersByTime(600)
  })
  expect(onBarClick).not.toHaveBeenCalled()
})

it('clears the pending single-click timer when the chart unmounts', async () => {
  const onBarClick = vi.fn()
  await act(async () => root.render(<CanvasMarketChart
    bars={[{ time: '2026-10-16', open: 10, high: 12, low: 9, close: 11, volume: null }]}
    symbol="TEST" contextKey="TEST" showControls={false} onBarClick={onBarClick}
  />))
  vi.useFakeTimers()
  const canvas = host.querySelector('canvas')!
  await act(async () => canvas.dispatchEvent(new MouseEvent('click', {
    bubbles: true, detail: 1, clientX: 292, clientY: 100,
  })))
  await act(async () => root.render(null))
  await act(async () => vi.advanceTimersByTime(600))
  expect(onBarClick).not.toHaveBeenCalled()
})

it('clears the previous candle summary after dragging and wheel zooming', async () => {
  const wheelAddSpy = vi.spyOn(HTMLCanvasElement.prototype, 'addEventListener')
  const onBarClick = vi.fn()
  const bars = Array.from({ length: 12 }, (_, index) => ({
    time: index + 1, open: 10 + index, high: 12 + index, low: 9 + index,
    close: 11 + index, volume: 100 + index,
  }))
  await act(async () => root.render(<CanvasMarketChart
    bars={bars} symbol="TEST" contextKey="TEST" visibleBars={5} showControls={false}
    formatPrice={value => typeof value === 'number' ? String(value) : '—'}
    onBarClick={onBarClick}
  />))
  vi.useFakeTimers()
  const canvas = host.querySelector('canvas')!
  const summary = () => host.querySelector('[aria-live="polite"]')!.textContent ?? ''
  await act(async () => canvas.dispatchEvent(new MouseEvent('pointermove', {
    bubbles: true, clientX: 292, clientY: 100,
  })))
  expect(summary()).toContain('收 20')

  await act(async () => {
    canvas.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 500, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0, clientX: 500, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1, clientX: 500, clientY: 100 }))
  })
  await act(async () => vi.advanceTimersByTime(600))
  expect(onBarClick).not.toHaveBeenCalled()
  expect(summary()).toContain('收 22')
  expect(summary()).not.toContain('收 20')

  await act(async () => canvas.dispatchEvent(new MouseEvent('pointermove', {
    bubbles: true, clientX: 292, clientY: 100,
  })))
  const beforeWheel = summary()
  const wheelEvent = new WheelEvent('wheel', {
    bubbles: true, cancelable: true, deltaY: 1, clientX: 292, clientY: 100,
  })
  await act(async () => canvas.dispatchEvent(wheelEvent))
  expect(wheelEvent.defaultPrevented).toBe(true)
  expect(wheelAddSpy.mock.calls.some(([type, , options]) =>
    type === 'wheel' && typeof options === 'object' && options?.passive === false,
  )).toBe(true)
  expect(beforeWheel).not.toBe(summary())
  expect(summary()).toContain('收 22')
})

it('syncs only series whose visible-by-default value changes', async () => {
  const bars = [{ time: 1, open: 10, high: 12, low: 9, close: 11, volume: null }]
  const makeSeries = (maDefault: boolean) => [
    { id: 'ma', label: 'MA', color: '#fff', values: [10], visibleByDefault: maDefault },
    { id: 'custom', label: '自定义', color: '#0f0', values: [10], visibleByDefault: false },
  ] as const
  await act(async () => root.render(<CanvasMarketChart bars={bars} symbol="TEST" contextKey="TEST" series={makeSeries(true)} />))
  const checkbox = (label: string) => host.querySelector<HTMLInputElement>(`input[aria-label="显示${label}"]`)!
  expect(checkbox('MA').checked).toBe(true)
  expect(checkbox('自定义').checked).toBe(false)
  await act(async () => checkbox('自定义').click())
  expect(checkbox('自定义').checked).toBe(true)

  await act(async () => root.render(<CanvasMarketChart bars={bars} symbol="TEST" contextKey="TEST" series={makeSeries(false)} />))
  expect(checkbox('MA').checked).toBe(false)
  expect(checkbox('自定义').checked).toBe(true)

  await act(async () => root.render(<CanvasMarketChart bars={bars} symbol="TEST" contextKey="TEST" series={makeSeries(true)} />))
  expect(checkbox('MA').checked).toBe(true)
  expect(checkbox('自定义').checked).toBe(true)
  await act(async () => checkbox('MA').click())
  await act(async () => root.render(<CanvasMarketChart bars={bars} symbol="TEST" contextKey="TEST" series={makeSeries(true)} />))
  expect(checkbox('MA').checked).toBe(false)
  expect(checkbox('自定义').checked).toBe(true)
})

it('recomputes backing canvas size when DPR changes while ResizeObserver is active', async () => {
  type TestMediaQuery = {
    addEventListener: ReturnType<typeof vi.fn>
    removeEventListener: ReturnType<typeof vi.fn>
    listener?: (event: MediaQueryListEvent) => void
  }
  const mediaQueries: TestMediaQuery[] = []
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 1 })
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(() => {
      const media: TestMediaQuery = {
        addEventListener: vi.fn((_type: string, listener: (event: MediaQueryListEvent) => void) => { media.listener = listener }),
        removeEventListener: vi.fn(),
      }
      mediaQueries.push(media)
      return media
    }),
  })
  class TestResizeObserver {
    constructor(_callback: ResizeObserverCallback) {}
    observe() {}
    disconnect() {}
  }
  vi.stubGlobal('ResizeObserver', TestResizeObserver)

  await act(async () => root.render(<CanvasMarketChart
    bars={[{ time: 1, open: 10, high: 12, low: 9, close: 11, volume: null }]}
    symbol="TEST" contextKey="TEST" showControls={false}
  />))
  const canvas = host.querySelector('canvas')!
  expect(canvas.width).toBe(640)
  expect(mediaQueries.length).toBeGreaterThan(0)

  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 })
  await act(async () => mediaQueries[0].listener?.({ matches: false, media: '(resolution: 1dppx)' } as MediaQueryListEvent))
  expect(canvas.width).toBe(1280)
})
