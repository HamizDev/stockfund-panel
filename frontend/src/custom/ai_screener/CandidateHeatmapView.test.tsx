// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CandidateHeatmapView } from './CandidateHeatmapView'
import type { Candidate } from './client'

function candidate(symbol: string, name: string, change: number | null, amount: number | null, live = false): Candidate {
  return { symbol, name, strategies: [], hit_count: 1, price_source: live ? 'live' : 'daily', metrics: {
    close: 1, change_pct: change, amount, volume: null, turnover_rate: null, pe_ttm: null,
    pb: null, ma5: null, ma20: null, ma60: null,
  } }
}
const candidates = [
  { item: candidate('510300.SH', '沪深300ETF', 0.01, 3e8, true), rank: 1 },
  { item: candidate('518880.SH', '黄金ETF', -0.02, 1e8), rank: 2 },
  { item: candidate('511360.SH', '短融ETF', null, null), rank: 3 },
]
let host: HTMLDivElement
let root: Root
const select = vi.fn()
const changeWeight = vi.fn()
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  select.mockClear(); changeWeight.mockClear()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })

async function render(weight: 'equal' | 'amount' = 'equal') {
  await act(async () => root.render(<CandidateHeatmapView candidates={candidates} weight={weight} selectedSymbol="518880.SH" asOf="2026-10-16" onSelect={select} onWeightChange={changeWeight} />))
}

it('shows candidate names, decimal-unit changes and independent missing/source counts', async () => {
  await render()
  expect(host.textContent).toContain('沪深300ETF')
  expect(host.textContent).toContain('+1.00%')
  expect(host.textContent).toContain('-2.00%')
  expect(host.textContent).toContain('上涨 1')
  expect(host.textContent).toContain('下跌 1')
  expect(host.textContent).toContain('平盘 0')
  expect(host.textContent).toContain('涨跌暂缺 1')
  expect(host.textContent).toContain('实时报价 1 只、盘后报价 2 只')
  expect(host.querySelectorAll('[aria-label="候选分布图"] button')).toHaveLength(3)
})

it('uses the existing selection callback and changes presentation without fetching', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('Unexpected fetch'))
  try {
    await render()
    const gold = host.querySelector<HTMLButtonElement>('[aria-label="候选分布图"] button[aria-pressed="true"]')!
    await act(async () => gold.click())
    expect(select).toHaveBeenCalledExactlyOnceWith('518880.SH')
    const selector = host.querySelector<HTMLSelectElement>('[aria-label="热力图面积依据"]')!
    await act(async () => { selector.value = 'amount'; selector.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(changeWeight).toHaveBeenCalledExactlyOnceWith('amount')
    expect(fetch).not.toHaveBeenCalled()
  } finally { fetch.mockRestore() }
})

it('does not invent amounts and keeps excluded candidates selectable', async () => {
  await render('amount')
  expect(host.querySelectorAll('[aria-label="候选分布图"] button')).toHaveLength(2)
  expect(host.textContent).toContain('未绘制 1 只')
  const missing = host.querySelector<HTMLButtonElement>('[aria-label="查看短融ETF的候选详情"]')!
  await act(async () => missing.click())
  expect(select).toHaveBeenCalledWith('511360.SH')
})

it('keeps tiny candidates in one accessible name button rather than a second tiny focus target', async () => {
  const uneven = [candidates[0], { ...candidates[1], item: { ...candidates[1].item, metrics: { ...candidates[1].item.metrics, amount: 1e-300 } } }]
  await act(async () => root.render(<CandidateHeatmapView candidates={uneven} weight="amount" selectedSymbol={null} asOf={null} onSelect={select} onWeightChange={changeWeight} />))
  expect(host.querySelectorAll('[aria-label="候选分布图"] button')).toHaveLength(1)
  const small = host.querySelector<HTMLButtonElement>('[aria-label="查看黄金ETF的候选详情"]')!
  expect(small).not.toBeNull()
  await act(async () => small.click())
  expect(select).toHaveBeenCalledExactlyOnceWith('518880.SH')
})

it('shows a clear empty amount state and disconnects its resize observer', async () => {
  const disconnect = vi.fn()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() { disconnect() } })
  try {
    await act(async () => root.render(<CandidateHeatmapView candidates={candidates.slice(2)} weight="amount" selectedSymbol={null} asOf={null} onSelect={select} onWeightChange={changeWeight} />))
    expect(host.textContent).toContain('暂无正成交额可用于面积布局')
    expect(host.textContent).toContain('策略日期 未知')
    await act(async () => root.render(null))
    expect(disconnect).toHaveBeenCalledOnce()
  } finally { vi.unstubAllGlobals() }
})
