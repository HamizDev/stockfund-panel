// @vitest-environment jsdom
import { act, type ComponentProps } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AnalysisMinutePanel } from './AnalysisMinutePanel'
import { QK } from '@/lib/queryKeys'

const mocks = vi.hoisted(() => ({
  klineMinute: vi.fn(),
  indexMinute: vi.fn(),
  klineDaily: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  api: {
    klineMinute: mocks.klineMinute,
    indexMinute: mocks.indexMinute,
    klineDaily: mocks.klineDaily,
  },
}))

vi.mock('@/components/EChartsIntraday', () => ({
  EChartsIntraday: (props: Record<string, any>) => <div
    data-testid="intraday"
    data-date={props.date ?? ''}
    data-prev-close={props.prevClose ?? ''}
    data-price-limit={props.priceLimit?.limit_up ?? ''}
    data-points-only={String(!!props.pricePointsOnly)}
  >{props.data.map((row: { datetime: string; close: number }, index: number) => (
    <span key={index}>{row.datetime}|{row.close};</span>
  ))}</div>,
}))

vi.mock('@/components/CanvasMarketChart', () => ({
  CanvasMarketChart: (props: Record<string, any>) => <div
    data-testid="candles"
    data-height={props.height}
    data-context={props.contextKey}
    data-time={props.bars[0]?.time ?? ''}
    data-open={props.bars[0]?.open ?? ''}
    data-close-price={props.formatPrice(props.bars[0]?.close)}
    data-volume={props.bars[0]?.volume ?? ''}
  />,
}))

type Props = ComponentProps<typeof AnalysisMinutePanel>
type PendingCall = { symbol: string; date?: string; signal?: AbortSignal; resolve: (value: any) => void }

let host: HTMLDivElement
let root: Root
let client: QueryClient

const stockResponse = (symbol: string, date = '2020-01-02', rows?: any[]) => ({
  symbol,
  date,
  rows: rows ?? [{ datetime: `${date} 09:35:00`, open: 10, high: 10.5, low: 9.8, close: 10.2, volume: 120, amount: 1224 }],
  source: 'local',
  asset_type: 'stock',
  prev_close: 9.9,
  price_limit: { rate: 0.1, limit_up: 10.89, limit_down: 8.91, source: 'rule' },
})

function baseProps(overrides: Partial<Props> = {}): Props {
  return {
    symbol: '600000.SH',
    assetType: 'stock',
    date: null,
    onDateChange: vi.fn(),
    view: 'intraday',
    onViewChange: vi.fn(),
    ...overrides,
  }
}

async function render(props: Props) {
  await act(async () => root.render(<QueryClientProvider client={client}><AnalysisMinutePanel {...props} /></QueryClientProvider>))
  await settle()
}

async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  mocks.klineMinute.mockReset()
  mocks.indexMinute.mockReset()
  mocks.klineDaily.mockReset()
  mocks.klineMinute.mockImplementation(async (symbol: string) => stockResponse(symbol))
  mocks.indexMinute.mockImplementation(async (symbol: string) => ({
    symbol,
    date: '2020-01-02',
    rows: [{ datetime: '2020-01-02 09:35:00', open: null, high: null, low: null, close: 10, volume: null, amount: null }],
    source: 'live',
    provider: 'txquote',
    data_kind: 'price_points',
  }))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
})

it('routes stock and ETF through the shared live minute options, and latest mode does not request daily data', async () => {
  const onDateChange = vi.fn()
  await render(baseProps({ symbol: '600000.SH', assetType: 'stock', date: null, onDateChange }))
  expect(mocks.klineMinute).toHaveBeenCalledWith('600000.SH', undefined, true, expect.any(AbortSignal))
  expect(mocks.indexMinute).not.toHaveBeenCalled()
  expect(mocks.klineDaily).not.toHaveBeenCalled()
  expect(host.textContent).toContain('实际日期：2020-01-02')
  expect(host.textContent).toContain('数据状态：本地缓存')
  const intraday = host.querySelector<HTMLElement>('[data-testid="intraday"]')!
  expect(intraday.dataset.prevClose).toBe('9.9')
  expect(intraday.dataset.priceLimit).toBe('10.89')
  const latest = [...host.querySelectorAll('button')].find(button => button.textContent === '最新交易日')!
  await act(async () => latest.click())
  expect(onDateChange).toHaveBeenCalledWith(null)

  await render(baseProps({ symbol: '510300.SH', assetType: 'etf', date: '2020-01-03' }))
  expect(mocks.klineMinute).toHaveBeenLastCalledWith('510300.SH', '2020-01-03', true, expect.any(AbortSignal))
  expect(mocks.klineDaily).not.toHaveBeenCalled()
})

it('routes index to the price-points endpoint and disables 1-minute candles', async () => {
  await render(baseProps({ symbol: '000001.SH', assetType: 'index', date: '2020-01-02', view: 'candles' }))
  expect(mocks.indexMinute).toHaveBeenCalledWith('000001.SH', '2020-01-02', expect.any(AbortSignal))
  expect(mocks.klineMinute).not.toHaveBeenCalled()
  const intraday = host.querySelector<HTMLElement>('[data-testid="intraday"]')!
  expect(intraday.dataset.pointsOnly).toBe('true')
  const candlesButton = [...host.querySelectorAll('button')].find(button => button.textContent === '1分钟K')!
  expect(candlesButton.disabled).toBe(true)
  expect(candlesButton.getAttribute('aria-pressed')).toBe('false')
  const intradayButton = [...host.querySelectorAll('button')].find(button => button.textContent === '分时')!
  expect(intradayButton.getAttribute('aria-pressed')).toBe('true')
  expect(host.querySelector('[data-testid="candles"]')).toBeNull()
  expect(host.textContent).toContain('数据状态：在线查询')
  expect(host.textContent).not.toContain('txquote')
})

it('does not draw candles when real OHLC is incomplete and does not synthesize open from close', async () => {
  mocks.klineMinute.mockResolvedValueOnce(stockResponse('600000.SH', '2020-01-02', [
    { datetime: '2020-01-02 09:35:00', open: null, high: 10.5, low: 9.8, close: 10.2, volume: 120, amount: 1224 },
  ]))
  await render(baseProps({ view: 'candles' }))
  expect(host.querySelector('[data-testid="candles"]')).toBeNull()
  expect(host.textContent).toContain('缺少有效的真实分钟 OHLC')
  expect(host.textContent).toContain('不会用收盘价补开盘价')

  const props = baseProps({ view: 'intraday' })
  await render(props)
  expect(host.querySelector('[data-testid="intraday"]')).not.toBeNull()
})

it('formats valid candle time from the Beijing wall-clock string and passes actual OHLC through', async () => {
  mocks.klineMinute.mockResolvedValueOnce(stockResponse('600000.SH', '2020-01-02', [
    { datetime: '2020-01-02T09:35:00', open: 10.1, high: 10.5, low: 9.9, close: 10.2, volume: 120, amount: 1224 },
  ]))
  await render(baseProps({ view: 'candles' }))
  const candles = host.querySelector<HTMLElement>('[data-testid="candles"]')!
  expect(candles.dataset.time).toBe('09:35')
  expect(candles.dataset.open).toBe('10.1')
  expect(candles.dataset.closePrice).toBe('10.20')
  expect(candles.dataset.volume).toBe('120')
})

it('does not show a response that belongs to a previous symbol or date', async () => {
  const pending: PendingCall[] = []
  mocks.klineMinute.mockImplementation((symbol: string, date?: string, _live?: boolean, signal?: AbortSignal) => new Promise(resolve => pending.push({ symbol, date, signal, resolve })))
  const first = baseProps({ symbol: '600000.SH', date: '2020-01-02' })
  await act(async () => root.render(<QueryClientProvider client={client}><AnalysisMinutePanel {...first} /></QueryClientProvider>))
  await settle()

  const second = baseProps({ symbol: '000002.SZ', date: '2020-01-03' })
  await act(async () => root.render(<QueryClientProvider client={client}><AnalysisMinutePanel {...second} /></QueryClientProvider>))
  await settle()
  expect(pending.map(call => [call.symbol, call.date])).toEqual([
    ['600000.SH', '2020-01-02'],
    ['000002.SZ', '2020-01-03'],
  ])

  expect(pending[0].signal?.aborted).toBe(true)
  expect(pending[1].signal?.aborted).toBe(false)

  await act(async () => pending[0].resolve(stockResponse('600000.SH', '2020-01-02')))
  await settle()
  expect(host.querySelector('[data-testid="intraday"]')).toBeNull()

  await act(async () => pending[1].resolve(stockResponse('000002.SZ', '2020-01-03')))
  await settle()
  expect(host.querySelector('[data-testid="intraday"]')?.textContent).toContain('2020-01-03 09:35:00|10.2')
})

it('shows an error and retries after the initial request fails', async () => {
  mocks.klineMinute.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(stockResponse('600000.SH'))
  await render(baseProps())
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('加载失败')
  const retry = [...host.querySelectorAll('button')].find(button => button.textContent === '重试')!
  await act(async () => retry.click())
  await settle()
  expect(host.querySelector('[data-testid="intraday"]')).not.toBeNull()
  expect(mocks.klineMinute).toHaveBeenCalledTimes(2)
})

it('keeps same-context data visible and marks a failed refresh', async () => {
  mocks.klineMinute.mockResolvedValueOnce(stockResponse('600000.SH')).mockRejectedValueOnce(new Error('offline'))
  await render(baseProps())
  expect(host.querySelector('[data-testid="intraday"]')).not.toBeNull()
  const refresh = host.querySelector<HTMLButtonElement>('button[aria-label="刷新分钟数据"]')!
  await act(async () => refresh.click())
  await settle()
  expect(host.querySelector('[data-testid="intraday"]')).not.toBeNull()
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('仍显示上次有效数据')
})

it('keeps same-date minute rows when a successful refresh returns an empty response', async () => {
  const date = '2020-01-02'
  mocks.klineMinute.mockResolvedValueOnce(stockResponse('600000.SH', date))
    .mockResolvedValueOnce(stockResponse('600000.SH', date, []))
  await render(baseProps({ date }))
  expect(host.querySelector('[data-testid="intraday"]')?.textContent).toContain('2020-01-02 09:35:00|10.2')

  const refresh = host.querySelector<HTMLButtonElement>('button[aria-label="刷新分钟数据"]')!
  await act(async () => refresh.click())
  await settle()

  expect(host.querySelector('[data-testid="intraday"]')?.textContent).toContain('2020-01-02 09:35:00|10.2')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('仍显示上次有效数据')
})

it('does not carry previous-session minute rows into an empty latest response for a new date', async () => {
  mocks.klineMinute.mockResolvedValueOnce(stockResponse('600000.SH', '2020-01-02'))
    .mockResolvedValueOnce(stockResponse('600000.SH', '2020-01-03', []))
  await render(baseProps({ date: null }))
  expect(host.querySelector('[data-testid="intraday"]')?.textContent).toContain('2020-01-02 09:35:00|10.2')

  const refresh = host.querySelector<HTMLButtonElement>('button[aria-label="刷新分钟数据"]')!
  await act(async () => refresh.click())
  await settle()

  expect(host.querySelector('[data-testid="intraday"]')).toBeNull()
  expect(host.textContent).toContain('实际日期：2020-01-03')
  expect(host.textContent).toContain('暂无分钟数据')
  expect(host.querySelector('[role="alert"]')).toBeNull()
})

it('shows the minute-source warning returned with the current response', async () => {
  mocks.klineMinute.mockResolvedValueOnce({
    ...stockResponse('510300.SH', '2020-01-02'),
    asset_type: 'etf',
    minute_provider: 'txquote',
    minute_warning: 'ETF 备选分钟行情不含成交额。',
  })
  await render(baseProps({ symbol: '510300.SH', assetType: 'etf', date: '2020-01-02' }))

  expect(host.textContent).toContain('ETF 备选分钟行情不含成交额。')
})

it('waits without issuing a query until the asset type is recognized', async () => {
  await render(baseProps({ assetType: undefined }))
  expect(host.textContent).toContain('资产类型待识别')
  expect(mocks.klineMinute).not.toHaveBeenCalled()
  expect(mocks.indexMinute).not.toHaveBeenCalled()
})

it.each(['stock', 'etf'] as const)('refreshes an already-selected latest day for %s and displays its actual date', async assetType => {
  await render(baseProps({ assetType }))
  const latest = [...host.querySelectorAll('button')].find(button => button.textContent === '最新交易日')!
  await act(async () => latest.click())
  await settle()
  expect(mocks.klineMinute).toHaveBeenCalledTimes(2)
  expect(mocks.klineMinute).toHaveBeenLastCalledWith('600000.SH', undefined, true, expect.any(AbortSignal))
  expect(host.querySelector<HTMLInputElement>('input[type="date"]')?.value).toBe('2020-01-02')
  expect(latest.getAttribute('aria-pressed')).toBe('true')
  expect(host.textContent).toContain('最新交易日模式')
})

it('refreshes latest index price points without issuing a stock request', async () => {
  await render(baseProps({ symbol: '000001.SH', assetType: 'index' }))
  const latest = [...host.querySelectorAll('button')].find(button => button.textContent === '最新交易日')!
  await act(async () => latest.click())
  await settle()
  expect(mocks.indexMinute).toHaveBeenCalledTimes(2)
  expect(mocks.indexMinute).toHaveBeenLastCalledWith('000001.SH', undefined, expect.any(AbortSignal))
  expect(mocks.klineMinute).not.toHaveBeenCalled()
})

it('invalidates only the exact latest cache before switching away from a historical date', async () => {
  const symbol = '600000.SH'
  const latestKey = QK.analysisMinute(symbol, '', 'stock')
  client.setQueryData(latestKey, stockResponse(symbol, '2020-01-02'))
  const untouchedKey = QK.klineMinute('000002.SZ', '', true)
  client.setQueryData(untouchedKey, stockResponse('000002.SZ'))
  mocks.klineMinute.mockImplementation(async (_symbol: string, date?: string) => stockResponse(symbol, date ?? '2020-01-03'))
  const onDateChange = vi.fn()
  await render(baseProps({ date: '2020-01-01', onDateChange }))
  const latest = [...host.querySelectorAll('button')].find(button => button.textContent === '最新交易日')!
  await act(async () => latest.click())
  expect(onDateChange).toHaveBeenCalledWith(null)
  expect(client.getQueryState(latestKey)?.isInvalidated).toBe(true)
  expect(client.getQueryState(untouchedKey)?.isInvalidated).toBe(false)
  expect(mocks.klineMinute).toHaveBeenCalledTimes(1)
  await render(baseProps({ date: null }))
  expect(mocks.klineMinute).toHaveBeenCalledTimes(2)
  expect(mocks.klineMinute).toHaveBeenLastCalledWith(symbol, undefined, true, expect.any(AbortSignal))
  expect(host.textContent).toContain('实际日期：2020-01-03')
})
