// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { AnalysisKChart } from './AnalysisKChart'

const mock = vi.hoisted(() => ({ handlers: new Map<string, (event: any) => void>(), init: vi.fn() }))
vi.mock('echarts', () => ({ init: mock.init }))

it('uses refreshed dates and callbacks in the once-registered ECharts click event', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  mock.init.mockReturnValue({ on: (name: string, callback: (event: any) => void) => mock.handlers.set(name, callback),
    setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), dispatchAction: vi.fn() })
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), oldCallback = vi.fn(), newCallback = vi.fn()
  const row = (date: string) => ({ date, open: 1, high: 2, low: .5, close: 1.5, volume: 100 })
  try {
    await act(async () => root.render(<AnalysisKChart rows={[row('2026-10-14')]} onDateClick={oldCallback} />))
    await act(async () => root.render(<AnalysisKChart rows={[row('2026-10-16')]} onDateClick={newCallback} />))
    await act(async () => mock.handlers.get('click')!({ componentType: 'series', seriesType: 'candlestick', dataIndex: 0 }))
    expect(oldCallback).not.toHaveBeenCalled()
    expect(newCallback).toHaveBeenCalledWith('2026-10-16')
  } finally { await act(async () => root.unmount()); host.remove() }
})
