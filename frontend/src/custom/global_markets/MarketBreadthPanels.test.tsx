// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { BroadMarketSummary, CoreTechnicalPanels, Distribution } from './MarketBreadthPanels'
import type { GlobalMarketOverviewItem, GlobalMarketSnapshot, GlobalSnapshotState, MarketSnapshotItem } from './types'

const quoteTime = Date.parse('2026-10-16T12:00:00Z')

function overviewItem(overrides: Partial<GlobalMarketOverviewItem> = {}): GlobalMarketOverviewItem {
  return {
    symbol: 'AAPL', name: 'Apple', market: 'us', kind: 'stock', price: 100, change_pct: 0.02,
    quote_at_ms: quoteTime, fetched_at_ms: quoteTime, stale: false, error: null, status: 'ok',
    ...overrides,
  }
}

function snapshot(overrides: Partial<GlobalMarketSnapshot> = {}): GlobalMarketSnapshot {
  return {
    market: 'hk', currency: 'HKD', timezone: 'Asia/Hong_Kong', scope: 'hk_sina_hkd_pool',
    universe_label: '港股港币证券覆盖池（新浪×HKEX）', total_reported: 1, received: 1, complete: true,
    skipped: 0, date: '2026-10-16', source: 'sina_hk_public', source_note: '新浪源时延未知；成交额保留原值，未另声明单位，不能与其他来源金额直接比较。',
    fetched_at_ms: quoteTime, quote_at_ms: quoteTime,
    stats: { covered: 1, up: 1, flat: 0, down: 0, strong: 0, weak: 0, mean: 0.02, median: 0.02, bins: [0, 0, 0, 0, 0, 1, 0, 0] },
    ranks: { gainers: [], losers: [], amount: [], turnover: [] }, industries: [], industry_covered: 0,
    ...overrides,
  }
}

function snapshotState(data: GlobalMarketSnapshot): GlobalSnapshotState {
  return { data, loading: false, stale: false, error: null }
}

function snapshotItem(overrides: Partial<MarketSnapshotItem> = {}): MarketSnapshotItem {
  return {
    market: 'us', symbol: 'AAPL', name: 'Apple', kind: 'security', price: 100,
    change_pct: 0.02, amount: null, volume: 1200, turnover_rate: null, industry: null,
    quote_at_ms: quoteTime, fetched_at_ms: quoteTime, source: 'yahoo_screener', scope: 'us_screener_pool',
    ...overrides,
  }
}

let host: HTMLDivElement
let root: Root

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

async function render(content: ReactNode) {
  await act(async () => root.render(content))
}

it('renders supplied distribution bins and counts without converting them to percentages', async () => {
  await render(<Distribution bins={[0, 2, 0, 1, 3, 0, 1, 2]} up={5} flat={3} down={1} />)

  expect(host.querySelector('[role="img"]')?.getAttribute('aria-label'))
    .toBe('涨跌分布：0、2、0、1、3、0、1、2')
  expect(host.textContent).toContain('跌 1')
  expect(host.textContent).toContain('平 3')
  expect(host.textContent).toContain('涨 5')
})

it('shows unavailable snapshot coverage instead of fabricated all-zero breadth', async () => {
  const state: GlobalSnapshotState = { data: null, loading: true, stale: false, error: null }
  await render(<BroadMarketSummary state={state} onSelect={() => {}} />)

  expect(host.textContent).toContain('完整分页完成后显示统计')
  expect(host.textContent).toContain('正在读取行业分类与市场快照')
  expect(host.textContent).toContain('等待市场快照')
  expect(host.textContent).not.toContain('0/0/0')
  expect(host.querySelector('[role="img"]')).toBeNull()
})

it('does not count daily technicals whose bar date differs from the quote date', async () => {
  const olderDaily = {
    date: '2026-10-15', bars_count: 61,
    ma5: 99, ma20: 98, ma60: 97,
    above_ma5: true, above_ma20: true, above_ma60: true,
    return_5d: 0.1, return_20d: 0.2, volume_ratio_5: 4,
    new_high_60: true, new_low_60: false, range_position_60: 1,
  }
  await render(<CoreTechnicalPanels market="us" items={[overviewItem({ daily: olderDaily })]} onSelect={() => {}} />)

  expect(host.textContent).toContain('缺历史不计入对应分母 · 2026-10-16')
  expect(host.textContent).toContain('正在补齐核心观察池日K指标')
  expect(host.textContent).toContain('有效 0只')
  expect(host.textContent).not.toContain('4.00×')
  expect(host.textContent).not.toContain('+20.00%')
})

it('collapses unavailable broad statistics into a clear warning while core panels remain separate', async () => {
  await render(<BroadMarketSummary state={{ data: null, loading: false, stale: false, error: 'source failed' }} onSelect={() => {}} />)
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('未显示市场广度和全市场排行')
  expect(host.textContent).toContain('可继续查看产业篮子与核心观察池')
  expect(host.querySelector('h2')).toBeNull()
})

it('labels the Sina HKD pool, reports source amount without inventing its unit, and leaves unsupported fields unavailable', async () => {
  const tencent = snapshotItem({ market: 'hk', symbol: '00700.HK', name: '腾讯控股', kind: 'stock', source: 'sina_hk_public', scope: 'hk_sina_hkd_pool', amount: 15_000_000, volume: null })
  await render(<BroadMarketSummary state={snapshotState(snapshot({
    ranks: { gainers: [tencent], losers: [], amount: [tencent], turnover: [] },
  }))} onSelect={() => {}} />)

  expect(host.textContent).toContain('港股港币覆盖池 · 时延未知')
  expect(host.textContent).toContain('港股覆盖池涨跌分布')
  expect(host.textContent).toContain('不代表全港市场，ETF覆盖也不完整')
  expect(host.textContent).toContain('公开覆盖池未提供行业分类')
  expect(host.textContent).toContain('公开覆盖池未提供换手率')
  expect(host.textContent).toContain('成交额榜')
  expect(host.textContent).toContain('1500.0万原始值 · 单位待核')
  expect(host.textContent).toContain('成交额单位尚未核实，展示原始值')
  expect(host.textContent).not.toContain('HKD')
  expect(host.textContent).not.toContain('0 个源站行业')
  expect(host.textContent).not.toContain('0.00%')
})

it('reports incomplete Tencent ETF coverage in the composite Hong Kong snapshot metadata', async () => {
  await render(<BroadMarketSummary state={snapshotState(snapshot({
    scope: 'hk_sina_tencent_hkd_pool',
    universe_label: '港股港币证券与ETF覆盖池',
    source: 'sina_hk_public+tencent_etf',
    etf_coverage: { requested: 6, accepted: 4, unavailable: 2 },
    complete: false,
  }))} onSelect={() => {}} />)

  expect(host.textContent).toContain('港股港币覆盖池 · 时延未知')
  expect(host.textContent).toContain('港币ETF有效 4/6 · 部分报价缺失')
  expect(host.textContent).toContain('股票与港币ETF合并，证券身份已经目录核验')
  expect(host.textContent).not.toContain('本轮已取得')
  expect(host.textContent).not.toMatch(/新浪|腾讯ETF|HKEX|Nasdaq/)
})

it('keeps the Sina source amount rank and its original unit in a composite snapshot', async () => {
  const sinaRow = snapshotItem({
    market: 'hk', symbol: '00700.HK', name: '腾讯控股', kind: 'stock',
    source: 'sina_hk_public', scope: 'hk_sina_hkd_pool', amount: 15_000, volume: null,
  })
  await render(<BroadMarketSummary state={snapshotState(snapshot({
    scope: 'hk_sina_tencent_hkd_pool',
    source: 'sina_hk_public+tencent_etf',
    etf_coverage: { requested: 6, accepted: 6, unavailable: 0 },
    ranks: { gainers: [], losers: [], amount: [sinaRow], turnover: [] },
  }))} onSelect={() => {}} />)

  expect(host.textContent).toContain('成交额榜')
  expect(host.textContent).toContain('1.5万原始值 · 单位待核')
  expect(host.textContent).not.toContain('1.5万HKD')
})

it('keeps positive rank values red and negative rank values green through semantic color classes', async () => {
  const gainer = snapshotItem({ symbol: '00700.HK', name: '腾讯控股', market: 'hk', change_pct: 0.04 })
  const loser = snapshotItem({ symbol: '09988.HK', name: '阿里巴巴', market: 'hk', change_pct: -0.03 })
  await render(<BroadMarketSummary state={snapshotState(snapshot({
    ranks: { gainers: [gainer], losers: [loser], amount: [], turnover: [] },
  }))} onSelect={() => {}} />)

  const sections = [...host.querySelectorAll('section')]
  const gainSection = sections.find(section => section.querySelector('h2')?.textContent === '涨幅榜')
  const lossSection = sections.find(section => section.querySelector('h2')?.textContent === '跌幅榜')
  const gainValue = gainSection?.querySelector('button span.text-right > span')
  const lossValue = lossSection?.querySelector('button span.text-right > span')

  expect(gainValue?.classList.contains('text-bull')).toBe(true)
  expect(lossValue?.classList.contains('text-bear')).toBe(true)
})

it('uses real Yahoo screener volume and Nasdaq classification labels without turning volume into turnover', async () => {
  const apple = snapshotItem({ volume: 1200, market_cap: 2_200_000_000_000 })
  const data = snapshot({
    market: 'us', currency: 'USD', timezone: 'America/New_York', scope: 'us_screener_pool',
    universe_label: '美股公开筛选池', source: 'yahoo_screener', source_note: undefined,
    ranks: { gainers: [], losers: [], amount: [], turnover: [], volume: [apple], market_cap: [apple] },
    classification: { source: 'nasdaq_screener', source_as_of: '2026-10-19', fetched_at_ms: quoteTime, row_count: 6963, matched: 1, error: null },
  })
  await render(<BroadMarketSummary state={snapshotState(data)} onSelect={() => {}} />)

  expect(host.textContent).toContain('成交量榜')
  expect(host.textContent).toContain('总市值榜')
  expect(host.textContent).toContain('1,200')
  expect(host.textContent).toContain('22000.00亿')
  expect(host.textContent).toContain('行业目录 × 当前报价池 · 报价池匹配 1/1 只 · 目录 6963 条')
  expect(host.textContent).toContain('分类目录日期：2026-10-19 · 不是行情报价日期')
  expect(host.textContent).not.toContain('换手率榜')
  expect(host.textContent).not.toContain('成交额榜')
})

it('shows classification-source errors without presenting the directory date as a quote date', async () => {
  await render(<BroadMarketSummary state={snapshotState(snapshot({
    market: 'us', currency: 'USD', timezone: 'America/New_York', scope: 'us_screener_pool', total_reported: 344,
    source: 'yahoo_screener', source_note: undefined, classification: {
      source: 'nasdaq_screener', source_as_of: null, fetched_at_ms: quoteTime,
      row_count: 6963, matched: 0, error: '分类源暂不可用',
    },
  }))} onSelect={() => {}} />)

  expect(host.querySelector('[role="status"]')?.textContent).toContain('行业分类更新失败，暂用已有分类。')
  expect(host.textContent).toContain('分类目录日期：源站未给分类日期')
  expect(host.textContent).toContain('报价池匹配 0/344 只 · 目录 6963 条')
})
