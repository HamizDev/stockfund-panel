// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { BroadMarketSummary, CoreTechnicalPanels, Distribution } from './MarketBreadthPanels'
import type { GlobalMarketOverviewItem, GlobalSnapshotState } from './types'

const quoteTime = Date.parse('2026-10-16T12:00:00Z')

function overviewItem(overrides: Partial<GlobalMarketOverviewItem> = {}): GlobalMarketOverviewItem {
  return {
    symbol: 'AAPL', name: 'Apple', market: 'us', kind: 'stock', price: 100, change_pct: 0.02,
    quote_at_ms: quoteTime, fetched_at_ms: quoteTime, stale: false, error: null, status: 'ok',
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
