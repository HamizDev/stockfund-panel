// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider, type UseQueryOptions } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { QK } from '@/lib/queryKeys'
import { INSTRUMENT_VIEW_GC_MS } from '@/lib/useInstrumentViewCache'
import type { ResearchSentiment, ResearchSentimentSource, ResearchSupplement, ResearchSourcesStatus } from './types'
import { ResearchSupplementPanel } from './ResearchSupplementPanel'

const fixtures = vi.hoisted(() => ({
  sources: vi.fn(),
  supplement: vi.fn(),
  sentiment: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ api: {
  globalResearchSources: fixtures.sources,
  globalResearchSupplement: fixtures.supplement,
  globalResearchSentiment: fixtures.sentiment,
} }))

function sourcesStatus(configured: Array<'finnhub' | 'adanos'> = []): ResearchSourcesStatus {
  return {
    storage: 'local_json',
    sources: (['finnhub', 'adanos'] as const).map(provider => ({
      provider,
      configured: configured.includes(provider),
      masked: configured.includes(provider) ? '••••last' : '',
    })),
  }
}

function supplementFor(symbol: string): ResearchSupplement {
  return {
    market: 'us',
    symbol,
    currency: 'USD',
    quote: {
      price: 222.2,
      change_pct: 0.015,
      previous_close: 218,
      as_of_ms: Date.parse('2026-10-20T14:30:00Z'),
    },
    profile: { name: 'Apple Inc.', industry: 'Technology', market_cap_usd: 3_000_000_000_000 },
    news: [{
      headline: 'Company update',
      url: 'https://example.test/news',
      published_at_ms: Date.parse('2026-10-20T14:00:00Z'),
      summary: 'A short public summary.',
    }],
    warnings: ['新闻为摘要信息'],
    cached: false,
    fetched_at_ms: Date.parse('2026-10-20T14:31:00Z'),
  }
}

function sentimentFor(symbol: string, source: ResearchSentimentSource): ResearchSentiment {
  return {
    market: 'us',
    symbol,
    source,
    buzz_score: source === 'x' ? 64 : null,
    bullish_pct: null,
    mentions: source === 'x' ? 12 : null,
    metric: 'mentions',
    period_days: source === 'x' ? 7 : null,
    cached: false,
    warnings: source === 'news' ? ['所选标的没有匹配到舆情数据'] : [],
    fetched_at_ms: Date.parse('2026-10-20T14:31:00Z'),
  }
}

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixtures.sources.mockReset().mockResolvedValue(sourcesStatus())
  fixtures.supplement.mockReset().mockImplementation(async (symbol: string) => supplementFor(symbol))
  fixtures.sentiment.mockReset().mockImplementation(async (symbol: string, source: ResearchSentimentSource) => sentimentFor(symbol, source))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient.clear()
  host.remove()
})

async function render(symbol: string) {
  await act(async () => root.render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <ResearchSupplementPanel symbol={symbol} />
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

it('links to local source settings when neither provider is configured and makes no data requests', async () => {
  await render('AAPL')

  expect(host.querySelector('a[href="/settings?tab=data-sources&highlight=global-research"]')).not.toBeNull()
  expect(host.textContent).toContain('尚未配置补充数据源')
  expect(fixtures.supplement).not.toHaveBeenCalled()
  expect(fixtures.sentiment).not.toHaveBeenCalled()
})

it('loads configured Finnhub data on demand without polling and formats New York timestamps', async () => {
  fixtures.sources.mockResolvedValue(sourcesStatus(['finnhub']))

  await render('AAPL')

  expect(fixtures.supplement).toHaveBeenCalledTimes(1)
  expect(fixtures.supplement.mock.calls[0]?.[0]).toBe('AAPL')
  expect(fixtures.sentiment).not.toHaveBeenCalled()
  expect(host.textContent).toContain('$222.2')
  expect(host.textContent).toContain('$218')
  expect(host.textContent).toContain('Apple Inc.')
  expect(host.textContent).toContain('Technology')
  expect(host.textContent).toContain('2026/10/20 10:30')
  expect(host.textContent).toContain('新闻为摘要信息')
  expect(host.querySelector('.text-bull')?.textContent).toContain('1.5%')

  const entry = queryClient.getQueryCache().find({ queryKey: QK.globalResearchSupplement('AAPL') })
  expect((entry?.options as UseQueryOptions | undefined)?.staleTime).toBe(60_000)
  expect(entry?.options.gcTime).toBe(INSTRUMENT_VIEW_GC_MS)
  expect(entry?.options.retry).toBe(false)
  expect((entry?.options as UseQueryOptions | undefined)?.refetchOnWindowFocus).toBe(false)
  expect(entry?.meta).toEqual({ instrumentView: 'global-market-us', symbol: 'AAPL' })
})

it('shows em dashes when an optional quote timestamp or company field is unavailable', async () => {
  fixtures.sources.mockResolvedValue(sourcesStatus(['finnhub']))
  fixtures.supplement.mockResolvedValue({
    market: 'us',
    symbol: 'AAPL',
    currency: 'USD',
    quote: { price: 100, change_pct: null, previous_close: null, as_of_ms: null },
    profile: null,
    news: [],
    warnings: [],
    fetched_at_ms: Date.parse('2026-10-20T14:31:00Z'),
  } satisfies ResearchSupplement)

  await render('AAPL')

  expect(host.textContent).toContain('报价时间（纽约）—')
  expect(host.textContent).toContain('昨收—')
  expect(host.textContent).toContain('行业：—')
  expect(host.textContent).toContain('市值：—')
  expect(host.textContent).toContain('暂无新闻摘要')
})

it('keeps Adanos manual, queries only the selected source, and leaves unmatched sentiment fields empty', async () => {
  fixtures.sources.mockResolvedValue(sourcesStatus(['adanos']))

  await render('AAPL')
  expect(fixtures.sentiment).not.toHaveBeenCalled()

  const button = () => host.querySelector<HTMLButtonElement>('button[aria-label="手动查询所选来源舆情"]')!
  await act(async () => button().click())
  await settle()
  expect(fixtures.sentiment).toHaveBeenCalledTimes(1)
  expect(fixtures.sentiment.mock.calls[0]?.slice(0, 2)).toEqual(['AAPL', 'news'])
  expect(host.textContent).toContain('所选标的没有匹配到舆情数据')
  expect(host.textContent).toContain('看涨占比—')
  expect(host.textContent).not.toContain('0%')
  expect(button().disabled).toBe(true)
  const newsEntry = queryClient.getQueryCache().find({ queryKey: QK.globalResearchSentiment('AAPL', 'news') })
  expect(newsEntry?.options.retry).toBe(false)
  expect((newsEntry?.options as UseQueryOptions | undefined)?.refetchOnWindowFocus).toBe(false)
  expect(newsEntry?.meta).toEqual({ instrumentView: 'global-market-us', symbol: 'AAPL' })

  const select = host.querySelector<HTMLSelectElement>('select#research-sentiment-source')!
  await act(async () => {
    select.value = 'x'
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await settle()
  expect(fixtures.sentiment).toHaveBeenCalledTimes(1)
  await act(async () => button().click())
  await settle()
  expect(fixtures.sentiment).toHaveBeenCalledTimes(2)
  expect(fixtures.sentiment.mock.calls[1]?.slice(0, 2)).toEqual(['AAPL', 'x'])
  expect(host.textContent).toContain('64')
  expect(host.textContent).toContain('12')
})

it('uses the one-hour client cache and does not repeat a fresh manual query', async () => {
  fixtures.sources.mockResolvedValue(sourcesStatus(['adanos']))
  queryClient.setQueryData(QK.globalResearchSentiment('AAPL', 'news'), sentimentFor('AAPL', 'news'))

  await render('AAPL')

  expect(fixtures.sentiment).not.toHaveBeenCalled()
  expect(host.textContent).toContain('所选标的没有匹配到舆情数据')
  const entry = queryClient.getQueryCache().find({ queryKey: QK.globalResearchSentiment('AAPL', 'news') })
  expect((entry?.options as UseQueryOptions | undefined)?.staleTime).toBe(60 * 60_000)
  expect(entry?.options.gcTime).toBe(INSTRUMENT_VIEW_GC_MS)
})

it('does not show a previous symbol result after changing the selected stock', async () => {
  fixtures.sources.mockResolvedValue(sourcesStatus(['adanos']))
  queryClient.setQueryData(QK.globalResearchSentiment('AAPL', 'news'), {
    ...sentimentFor('AAPL', 'news'),
    buzz_score: 64,
    mentions: 12,
  })

  await render('AAPL')
  expect(host.textContent).toContain('64')

  await render('MSFT')
  expect(host.textContent).toContain('MSFT')
  expect(host.textContent).not.toContain('64')
  expect(fixtures.sentiment).not.toHaveBeenCalled()
})

it('does not request source credentials or data for index symbols', async () => {
  await render('^GSPC')

  expect(host.textContent).toContain('指数暂未支持补充数据')
  expect(fixtures.sources).not.toHaveBeenCalled()
  expect(fixtures.supplement).not.toHaveBeenCalled()
  expect(fixtures.sentiment).not.toHaveBeenCalled()
})
