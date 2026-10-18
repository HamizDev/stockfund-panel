// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { CanvasMarketChartProps } from '@/components/CanvasMarketChart'
import type { GlobalMarket, GlobalMarketChartInterval, GlobalMarketChartResponse } from './types'
import { MarketResearchPage } from './MarketResearchPage'

const fixtures = vi.hoisted(() => ({
  catalog: vi.fn(),
  chart: vi.fn(),
  resources: vi.fn(),
  catalogCalls: [] as { market: GlobalMarket; q: string }[],
  chartCalls: [] as { market: GlobalMarket; symbol: string; interval: GlobalMarketChartInterval }[],
  canvasProps: null as CanvasMarketChartProps | null,
}))

vi.mock('@/lib/api', () => ({
  api: {
    globalMarketCatalog: fixtures.catalog,
    globalMarketChart: fixtures.chart,
    globalMarketResources: fixtures.resources,
  },
}))

vi.mock('@/components/CanvasMarketChart', () => ({
  CanvasMarketChart: (props: CanvasMarketChartProps) => {
    fixtures.canvasProps = props
    return <div data-testid="canvas-chart" />
  },
}))

function itemsFor(market: GlobalMarket) {
  return market === 'hk'
    ? [
      { symbol: '00700.HK', name: '腾讯控股', market, kind: 'stock' as const },
      { symbol: '2800.HK', name: '盈富基金', market, kind: 'etf' as const },
      { symbol: 'HSI', name: '恒生指数', market, kind: 'index' as const },
    ]
    : [
      { symbol: 'SPY', name: 'SPDR S&P 500 ETF', market, kind: 'etf' as const },
      { symbol: 'AAPL', name: 'Apple Inc.', market, kind: 'stock' as const },
      { symbol: '^GSPC', name: 'S&P 500 Index', market, kind: 'index' as const },
    ]
}

function chartFor(
  market: GlobalMarket,
  symbol: string,
  interval: GlobalMarketChartInterval,
  overrides: Partial<GlobalMarketChartResponse> = {},
): GlobalMarketChartResponse {
  return {
    market,
    symbol,
    name: itemsFor(market).find(item => item.symbol === symbol)?.name ?? symbol,
    currency: market === 'hk' ? 'HKD' : 'USD',
    timezone: market === 'hk' ? 'Asia/Hong_Kong' : 'America/New_York',
    interval,
    source: 'yahoo_public',
    price_basis: 'source_ohlc',
    fetched_at_ms: Date.parse('2026-10-16T08:00:00Z'),
    quote_at_ms: Date.parse('2026-10-16T08:00:00Z'),
    cached: false,
    stale: false,
    error: null,
    warnings: ['源站OHLC：拆股按源站处理，未做分红复权；含当前未结束K线'],
    quote: { price: market === 'hk' ? 700 : 525, change_pct: 0.0366 },
    bars: [{
      time: interval === '1d' ? '2026-10-16' : Math.floor(Date.parse('2026-10-16T08:00:00Z') / 1_000),
      open: 100, high: 110, low: 90, close: 105, volume: 20,
    }],
    ...overrides,
  }
}

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  window.sessionStorage.clear()
  fixtures.catalogCalls.length = 0
  fixtures.chartCalls.length = 0
  fixtures.canvasProps = null
  fixtures.catalog.mockImplementation(async (market: GlobalMarket, q: string) => {
    fixtures.catalogCalls.push({ market, q })
    const items = itemsFor(market)
    const normalized = q.toLocaleLowerCase()
    return {
      market,
      items: normalized
        ? items.filter(item => `${item.symbol} ${item.name}`.toLocaleLowerCase().includes(normalized))
        : items,
      warnings: [],
    }
  })
  fixtures.chart.mockImplementation(async (market: GlobalMarket, symbol: string, interval: GlobalMarketChartInterval) => {
    fixtures.chartCalls.push({ market, symbol, interval })
    return chartFor(market, symbol, interval)
  })
  fixtures.resources.mockResolvedValue({ items: [] })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient.clear()
  host.remove()
  vi.useRealTimers()
})

async function renderMarket(market: GlobalMarket) {
  await act(async () => root.render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <MarketResearchPage market={market} />
      </QueryClientProvider>
    </MemoryRouter>,
  ))
  await settle()
}

async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

it('keeps HK and US queries separate, labels stock/ETF/index results, and formats each currency', async () => {
  await renderMarket('hk')
  expect(host.querySelector('h1')?.textContent).toBe('港股研究')
  expect(fixtures.chartCalls).toContainEqual({ market: 'hk', symbol: '00700.HK', interval: '1d' })
  expect(host.textContent).toContain('HK$700.00')
  expect(host.textContent).toContain('腾讯控股')
  expect(host.textContent).toContain('盈富基金')
  expect(host.textContent).toContain('恒生指数')
  expect(host.textContent).toContain('Yahoo Finance 公共接口')
  expect(host.textContent).toContain('15 分钟（日K）')
  expect(host.textContent).toContain('源站价格（未做分红复权）')
  expect(host.textContent).toContain('含当前未结束K线')
  expect(host.textContent).toContain('图表时间 Asia/Hong_Kong')
  expect(host.textContent).toContain('更新时间：2026/10/16 16:00')
  expect(host.textContent).toContain('报价时间：2026/10/16 16:00')
  expect(host.textContent).toContain('股票')
  expect(host.textContent).toContain('ETF')
  expect(host.textContent).toContain('指数')
  expect(fixtures.canvasProps?.upColor).toBe('#C74040')
  expect(fixtures.canvasProps?.downColor).toBe('#2D9B65')
  expect(fixtures.canvasProps?.bars[0]?.volume).toBe(20)

  await renderMarket('us')
  expect(host.querySelector('h1')?.textContent).toBe('美股研究')
  expect(fixtures.chartCalls).toContainEqual({ market: 'us', symbol: 'SPY', interval: '1d' })
  expect(host.textContent).toContain('$525.00')
  expect(host.textContent).toContain('SPDR S&P 500 ETF')
  expect(host.textContent).toContain('15 分钟（日K）')
  expect(host.textContent).not.toContain('腾讯控股')
  expect(host.textContent).not.toContain('00700.HK')
  const fiveMinutes = [...host.querySelectorAll<HTMLButtonElement>('button')]
    .find(button => button.textContent === '5分钟')
  expect(fiveMinutes).not.toBeUndefined()
  await act(async () => fiveMinutes!.click())
  await settle()
  expect(fixtures.chartCalls).toContainEqual({ market: 'us', symbol: 'SPY', interval: '5m' })
  expect(host.textContent).toContain('30 秒（分钟K）')
  expect(fixtures.catalogCalls).toContainEqual({ market: 'hk', q: '' })
  expect(fixtures.catalogCalls).toContainEqual({ market: 'us', q: '' })
})

it('restores the US selection and interval from its independent session state', async () => {
  window.sessionStorage.setItem('page-view:global-markets:us', JSON.stringify({ symbol: 'QQQ', interval: '5m' }))

  await renderMarket('us')

  expect(fixtures.chartCalls).toContainEqual({ market: 'us', symbol: 'QQQ', interval: '5m' })
  expect(host.textContent).toContain('QQQ')
  expect(host.textContent).toContain('5分钟')
  expect(window.sessionStorage.getItem('page-view:global-markets:us')).toContain('QQQ')
})

it('recovers invalid session state with the market defaults', async () => {
  window.sessionStorage.setItem('page-view:global-markets:hk', JSON.stringify({ symbol: 700, interval: '10m' }))

  await renderMarket('hk')

  expect(fixtures.chartCalls).toContainEqual({ market: 'hk', symbol: '00700.HK', interval: '1d' })
  expect(host.textContent).toContain('00700.HK')
  expect(host.textContent).toContain('日K')
  expect(window.sessionStorage.getItem('page-view:global-markets:hk')).toBe(JSON.stringify({ symbol: '00700.HK', interval: '1d' }))
})

it('shows an expired-cache notice and source error while keeping available candles visible', async () => {
  fixtures.chart.mockImplementationOnce(async (market: GlobalMarket, symbol: string, interval: GlobalMarketChartInterval) => {
    fixtures.chartCalls.push({ market, symbol, interval })
    return chartFor(market, symbol, interval, { stale: true, error: '上游暂时不可用' })
  })

  await renderMarket('hk')

  expect(host.textContent).toContain('数据源缓存已过期')
  expect(host.textContent).toContain('上游暂时不可用')
  expect(host.querySelector('[data-testid="canvas-chart"]')).not.toBeNull()
  expect(host.querySelector('[aria-label="刷新行情"]')).not.toBeNull()
})

it('shows a request failure and recovers when the user refreshes', async () => {
  fixtures.chart.mockRejectedValueOnce(new Error('网络不可用'))

  await renderMarket('hk')
  expect(host.textContent).toContain('行情加载失败：网络不可用')

  const refresh = host.querySelector<HTMLButtonElement>('[aria-label="刷新行情"]')
  expect(refresh).not.toBeNull()
  await act(async () => refresh!.click())
  await settle()
  expect(host.textContent).toContain('HK$700.00')
  expect(host.textContent).not.toContain('行情加载失败：网络不可用')
})

it('does not show an HK response that resolves after navigation to the US route', async () => {
  let resolveHongKong!: (value: GlobalMarketChartResponse) => void
  const delayedHongKong = new Promise<GlobalMarketChartResponse>(resolve => { resolveHongKong = resolve })
  fixtures.chart.mockImplementation((market: GlobalMarket, symbol: string, interval: GlobalMarketChartInterval) => {
    fixtures.chartCalls.push({ market, symbol, interval })
    return market === 'hk' ? delayedHongKong : Promise.resolve(chartFor(market, symbol, interval))
  })

  await renderMarket('hk')
  expect(host.textContent).toContain('港股研究')
  await renderMarket('us')
  expect(host.textContent).toContain('美股研究')
  expect(host.textContent).toContain('$525.00')

  await act(async () => resolveHongKong(chartFor('hk', '00700.HK', '1d')))
  await settle()
  expect(host.textContent).toContain('美股研究')
  expect(host.textContent).toContain('$525.00')
  expect(host.textContent).not.toContain('腾讯控股')
  expect(host.textContent).not.toContain('HK$700.00')
})

it('debounces catalog requests while typing a code or English name', async () => {
  await renderMarket('us')
  const input = host.querySelector<HTMLInputElement>('input[type="search"]')
  expect(input).not.toBeNull()
  const setNativeValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
  expect(setNativeValue).toBeDefined()

  await act(async () => {
    setNativeValue!.call(input, 'AAP')
    input!.dispatchEvent(new Event('input', { bubbles: true }))
    setNativeValue!.call(input, 'AAPL')
    input!.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise(resolve => setTimeout(resolve, 100))
  })
  expect(fixtures.catalogCalls.some(call => call.q === 'AAPL')).toBe(false)
  await act(async () => new Promise(resolve => setTimeout(resolve, 220)))
  await settle()
  expect(fixtures.catalogCalls).toContainEqual({ market: 'us', q: 'AAPL' })
  expect(host.textContent).toContain('Apple Inc.')
})

it('loads resource-directory scope only after opening the foldout and uses safe external links', async () => {
  fixtures.resources.mockResolvedValue({ items: [{
    id: 'sample', name: 'Public market data', url: 'https://example.test/market', category: '行情',
    free_scope: '有限免费公开接口', paid_scope: '高级历史数据需付费', access: 'free_web', markets: ['macro'],
  }] })

  await renderMarket('us')
  expect(fixtures.resources).not.toHaveBeenCalled()
  const foldout = host.querySelector<HTMLButtonElement>('button[aria-expanded="false"]')
  expect(foldout).not.toBeNull()
  await act(async () => foldout!.click())
  await settle()

  const link = host.querySelector<HTMLAnchorElement>('a[href="https://example.test/market"]')
  expect(link?.target).toBe('_blank')
  expect(link?.rel).toBe('noopener noreferrer')
  expect(host.textContent).toContain('有限免费公开接口')
  expect(host.textContent).toContain('高级历史数据需付费')

  await renderMarket('hk')
  const hkFoldout = host.querySelector<HTMLButtonElement>('button[aria-expanded="false"]')
  expect(hkFoldout).not.toBeNull()
  await act(async () => hkFoldout!.click())
  await settle()
  expect(host.querySelector('a[href="https://example.test/market"]')).not.toBeNull()
  expect(fixtures.resources).toHaveBeenCalledTimes(1)
})
