// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CanvasMarketChart } from './CanvasMarketChart'
import { sessionViewportMemory } from '@/lib/canvas-chart/viewportMemory'

const canvasContext = {
  setTransform: vi.fn(), clearRect: vi.fn(), fillRect: vi.fn(), strokeRect: vi.fn(),
  beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), fill: vi.fn(),
  arc: vi.fn(), closePath: vi.fn(), save: vi.fn(), restore: vi.fn(), setLineDash: vi.fn(),
  fillText: vi.fn(), measureText: vi.fn((text: string) => ({ width: text.length * 6 })),
} as unknown as CanvasRenderingContext2D
const hoverCanvasContext = {
  setTransform: vi.fn(), clearRect: vi.fn(), fillRect: vi.fn(), strokeRect: vi.fn(),
  beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), fill: vi.fn(),
  arc: vi.fn(), closePath: vi.fn(), save: vi.fn(), restore: vi.fn(), setLineDash: vi.fn(),
  fillText: vi.fn(), measureText: vi.fn((text: string) => ({ width: text.length * 6 })),
} as unknown as CanvasRenderingContext2D

function drawCallCount(context: CanvasRenderingContext2D): number {
  return Object.values(context as unknown as Record<string, any>)
    .reduce((total, method) => total + (method?.mock?.calls?.length ?? 0), 0)
}

let root: Root
let host: HTMLDivElement

beforeEach(() => {
  sessionStorage.clear()
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
    return (this.classList.contains('canvas-market-chart-hover') ? hoverCanvasContext : canvasContext) as any
  })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})

it('restores opt-in zoom and pan per context after unmount, rather than resetting to defaults', async () => {
  const bars = Array.from({ length: 100 }, (_, i) => ({ time: i + 1, open: 10, high: 12, low: 9, close: 11, volume: 10 }))
  const renderChart = async (contextKey: string) => act(async () => root.render(<CanvasMarketChart
    bars={bars} symbol="TEST" contextKey={contextKey} visibleBars={80} viewportMemory={sessionViewportMemory} />))
  await renderChart('hk:^HSI:1d')
  let canvas = host.querySelector('canvas')!
  await act(async () => canvas.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, clientX: 500, clientY: 100, cancelable: true })))
  await act(async () => {
    canvas.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 200, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, button: 0, clientX: 300, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0, clientX: 300, clientY: 100 }))
  })
  const saved = sessionViewportMemory.read('hk:^HSI:1d')!
  expect(saved.visibleCount).toBe(67)
  expect(saved.atLatest).toBe(false)
  expect(saved.endTime).toBeLessThan(100)
  await act(async () => root.render(<div />))
  await renderChart('us:^GSPC:1d')
  expect(sessionViewportMemory.read('us:^GSPC:1d')).toBeNull()
  await act(async () => root.render(<div />))
  await renderChart('hk:^HSI:1d')
  canvas = host.querySelector('canvas')!
  await act(async () => canvas.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, clientX: 500, clientY: 100, cancelable: true })))
  expect(sessionViewportMemory.read('hk:^HSI:1d')?.visibleCount).toBe(56)
  expect(sessionViewportMemory.read('hk:^HSI:1d')?.endTime).toBeLessThan(100)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

it('does not repaint the static chart for every pointer move', async () => {
  const bars = Array.from({ length: 240 }, (_, index) => ({
    time: index + 1,
    open: 10 + index * 0.01,
    high: 10.5 + index * 0.01,
    low: 9.5 + index * 0.01,
    close: 10.1 + index * 0.01,
    volume: 1000 + index,
  }))
  await act(async () => root.render(<CanvasMarketChart
    bars={bars} symbol="TEST" contextKey="hover-render-count" visibleBars="all" showControls={false}
  />))
  const canvas = host.querySelector('canvas')!
  const clearCallsBefore = (canvasContext.clearRect as any).mock.calls.length
  const drawingCallsBefore = drawCallCount(canvasContext)
  const overlayClearCallsBefore = (hoverCanvasContext.clearRect as any).mock.calls.length
  const overlayDrawingCallsBefore = drawCallCount(hoverCanvasContext)
  const overlayCrosshairsBefore = (hoverCanvasContext.stroke as any).mock.calls.length

  for (const clientX of [70, 160, 250, 340, 430]) {
    await act(async () => {
      canvas.dispatchEvent(new MouseEvent('pointermove', {
        bubbles: true, clientX, clientY: 120,
      }))
    })
  }

  const clearCallsAfter = (canvasContext.clearRect as any).mock.calls.length
  const drawingCallsAfter = drawCallCount(canvasContext)
  const overlayClearCallsAfter = (hoverCanvasContext.clearRect as any).mock.calls.length
  const overlayDrawingCallsAfter = drawCallCount(hoverCanvasContext)

  expect(clearCallsAfter).toBe(clearCallsBefore)
  expect(drawingCallsAfter).toBe(drawingCallsBefore)
  expect(overlayClearCallsAfter - overlayClearCallsBefore).toBe(5)
  expect(overlayDrawingCallsAfter).toBeGreaterThan(overlayDrawingCallsBefore)
  expect((hoverCanvasContext.stroke as any).mock.calls.length).toBeGreaterThan(overlayCrosshairsBefore)
  expect(host.querySelector('.canvas-market-chart-hover')?.getAttribute('aria-hidden')).toBe('true')
})

it('renders empty input safely and exposes an empty chart status', async () => {
  await act(async () => root.render(<CanvasMarketChart bars={[]} symbol="TEST" contextKey="TEST" showControls={false} />))
  expect(host.querySelector('canvas')?.getAttribute('aria-label')).toContain('Canvas K线图')
  expect(host.querySelector('[role="status"]')?.textContent).toContain('暂无有效 K 线数据')
  expect(HTMLCanvasElement.prototype.getContext).toHaveBeenCalled()
})

it('reports the hovered candle close and clears it on pointer exit, context/data changes, and unmount', async () => {
  const onPriceHover = vi.fn()
  const bars = [{ time: '2026-10-16', open: 10, high: 12, low: 9, close: 11, volume: 10 }]
  const renderChart = async (contextKey = 'TEST', nextBars = bars) => act(async () => root.render(<CanvasMarketChart
    bars={nextBars} symbol="TEST" contextKey={contextKey} showControls={false}
    onPriceHover={price => onPriceHover(price)}
  />))
  await renderChart()
  const canvas = host.querySelector('canvas')!

  await act(async () => canvas.dispatchEvent(new MouseEvent('pointermove', {
    bubbles: true, clientX: 292, clientY: 100,
  })))
  expect(onPriceHover).toHaveBeenLastCalledWith(11)

  // A new callback identity on an ordinary parent render must not replay the current value.
  const callsAfterHover = onPriceHover.mock.calls.length
  await renderChart()
  expect(onPriceHover).toHaveBeenCalledTimes(callsAfterHover)

  await act(async () => canvas.dispatchEvent(new MouseEvent('pointerout', { bubbles: true, relatedTarget: null })))
  expect(onPriceHover).toHaveBeenLastCalledWith(null)

  await act(async () => canvas.dispatchEvent(new MouseEvent('pointermove', {
    bubbles: true, clientX: 292, clientY: 100,
  })))
  expect(onPriceHover).toHaveBeenLastCalledWith(11)
  await act(async () => canvas.dispatchEvent(new MouseEvent('pointermove', {
    bubbles: true, clientX: 0, clientY: 100,
  })))
  expect(onPriceHover).toHaveBeenLastCalledWith(null)

  await act(async () => {
    canvas.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 292, clientY: 100 }))
    canvas.dispatchEvent(new MouseEvent('pointercancel', { bubbles: true, button: 0, clientX: 292, clientY: 100 }))
  })
  expect(onPriceHover).toHaveBeenLastCalledWith(null)

  await act(async () => canvas.dispatchEvent(new MouseEvent('pointermove', {
    bubbles: true, clientX: 292, clientY: 100,
  })))
  expect(onPriceHover).toHaveBeenLastCalledWith(11)
  await renderChart('OTHER')
  expect(onPriceHover).toHaveBeenLastCalledWith(null)

  const changedBars = [{ ...bars[0], close: 10.5 }]
  await act(async () => host.querySelector('canvas')!.dispatchEvent(new MouseEvent('pointermove', {
    bubbles: true, clientX: 292, clientY: 100,
  })))
  expect(onPriceHover).toHaveBeenLastCalledWith(11)
  await renderChart('OTHER', changedBars)
  expect(onPriceHover).toHaveBeenLastCalledWith(null)

  await act(async () => host.querySelector('canvas')!.dispatchEvent(new MouseEvent('pointermove', {
    bubbles: true, clientX: 292, clientY: 100,
  })))
  expect(onPriceHover).toHaveBeenLastCalledWith(10.5)
  await act(async () => root.unmount())
  expect(onPriceHover).toHaveBeenLastCalledWith(null)
  root = createRoot(host)
})

it('labels the linked price line with the formatted price', async () => {
  const fillText = vi.spyOn(canvasContext, 'fillText')
  await act(async () => root.render(<CanvasMarketChart
    bars={[{ time: 1, open: 0.5, high: 0.51, low: 0.49, close: 0.5, volume: 10 }]}
    symbol="TEST" contextKey="TEST" linkedPrice={0.525} showControls={false}
    formatPrice={value => typeof value === 'number' ? value.toFixed(3) : '—'}
  />))
  expect(fillText.mock.calls.some(([text]) => text === '分时价 0.525')).toBe(true)
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
  const hoverCanvas = host.querySelector<HTMLCanvasElement>('.canvas-market-chart-hover')!
  expect(canvas.width).toBe(640)
  expect(hoverCanvas.width).toBe(640)
  expect(mediaQueries.length).toBeGreaterThan(0)

  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 })
  await act(async () => mediaQueries[0].listener?.({ matches: false, media: '(resolution: 1dppx)' } as MediaQueryListEvent))
  expect(canvas.width).toBe(1280)
  expect(hoverCanvas.width).toBe(1280)
})
