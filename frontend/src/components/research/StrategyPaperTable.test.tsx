// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import type { PaperCompareRow, StrategyDetail } from '@/lib/api'
import { StrategyPaperTable } from './StrategyPaperTable'

type StrategyPaperRow = PaperCompareRow & {
  asset_type?: 'stock' | 'etf' | null
  wins?: number
  holdings_count?: number
  nav_date?: string | null
  settled_total?: number | null
  settled_pnl_pct?: number | null
  settled_max_drawdown?: number | null
  settled_nav_status?: 'complete' | 'missing_prices' | 'legacy_unknown' | 'unavailable'
}

const container = document.createElement('div')
document.body.appendChild(container)
let root: Root | null = null

function makeRow(overrides: Partial<StrategyPaperRow> = {}): StrategyPaperRow {
  return {
    account: 'paper-alpha',
    name: 'Alpha 虚拟账户',
    strategy_id: 'alpha',
    asset_type: 'stock',
    auto_enabled: false,
    status: 'active',
    initial_cash: 100_000,
    fees: { commission_pct: 0.0003, stamp_tax_pct: 0.0005, slippage_bps: 2 },
    total: 108_000,
    cash: 50_000,
    market_value: 58_000,
    total_pnl: 8_000,
    pnl_pct: 8,
    settled_total: 108_000,
    settled_pnl_pct: 8,
    settled_max_drawdown: 6,
    settled_nav_status: 'complete',
    nav_date: '2026-10-02',
    rounds: 5,
    wins: 3,
    win_rate: 60,
    profit_loss_ratio: 1.4,
    avg_holding_days: 4.2,
    realized_pnl: 4_000,
    max_drawdown: 6,
    holdings_count: 2,
    nav: [{ date: '2026-10-02', nav: 108_000 }],
    ...overrides,
  }
}

function makeStrategy(id: string, name: string): StrategyDetail {
  return {
    id,
    name,
    description: '',
    tags: [],
    source: 'custom',
    execution_backend: 'polars_expr',
    asset_types: ['stock', 'etf'],
    timeframes: ['1d'],
    version: '1',
    basic_filter: {},
    params: [],
    params_defaults: {},
    scoring: {},
    scoring_directions: {},
    entry_signals: [],
    exit_signals: [],
    minute_exit_trigger_supported_signals: [],
    stop_loss: null,
    take_profit: null,
    trailing_stop: null,
    trailing_take_profit_activate: null,
    trailing_take_profit_drawdown: null,
    max_hold_days: null,
    order_by: 'score',
    descending: true,
    limit: 20,
  }
}

const strategies = [makeStrategy('alpha', 'Alpha'), makeStrategy('beta', 'Beta'), makeStrategy('gamma', 'Gamma')]

function render(rows: PaperCompareRow[], onToggle = vi.fn(), togglePending = false) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  root = createRoot(container)
  act(() => {
    root!.render(
      <StrategyPaperTable
        rows={rows}
        strategies={strategies}
        onSelect={vi.fn()}
        onToggle={onToggle}
        togglePending={togglePending}
      />,
    )
  })
  return { onToggle }
}

function click(button: HTMLButtonElement) {
  act(() => button.click())
}

function accountOrder() {
  return Array.from(container.querySelectorAll<HTMLButtonElement>('button[aria-label^="查看账户 "]'))
    .map(button => button.getAttribute('aria-label')?.replace('查看账户 ', ''))
}

afterEach(() => {
  if (root) {
    act(() => root!.unmount())
    root = null
  }
  container.innerHTML = ''
})

it('shows an empty closed-round sample as — (0/0)', () => {
  render([makeRow({ rounds: 0, wins: 0, win_rate: 0 })])

  expect(container.textContent).toContain('— (0/0)')
})

it('shows unknown rounds as unknown instead of a fabricated 0/0 sample', () => {
  render([makeRow({ rounds: undefined, wins: undefined, win_rate: 0 })])

  expect(container.textContent).toContain('未知')
  expect(container.textContent).not.toContain('— (0/0)')
})

it('does not infer win counts from win rate when wins is absent', () => {
  render([makeRow({ rounds: 4, wins: undefined, win_rate: 75 })])

  expect(container.textContent).toContain('—/4 · 75.0%')
  expect(container.textContent).not.toContain('3/4')
})

it('does not use nonzero overview estimates when no settled NAV exists', () => {
  render([makeRow({
    total: 112_000,
    pnl_pct: 12,
    settled_total: null,
    settled_pnl_pct: null,
    settled_max_drawdown: null,
    settled_nav_status: 'unavailable',
    nav_date: null,
    nav: [],
  })])

  const row = container.querySelector('tbody tr')!
  expect(row.textContent).toContain('待盘后定版')
  expect(row.textContent).not.toContain('+12.00%')
  expect(row.textContent).not.toContain('¥112,000.00')
  expect(row.textContent).toContain('净值日 —')
})

it('hides legacy NAV values whose historical market-data quality is unknown', () => {
  render([makeRow({
    total: 109_000,
    pnl_pct: 9,
    settled_total: 109_000,
    settled_pnl_pct: 9,
    settled_max_drawdown: 8,
    settled_nav_status: 'legacy_unknown',
    nav: [{ date: '2026-10-02', nav: 109_000 }],
  })])

  const row = container.querySelector('tbody tr')!
  expect(row.textContent).toContain('历史净值待核对')
  expect(row.textContent).not.toContain('+9.00%')
  expect(row.textContent).not.toContain('¥109,000.00')
  expect(row.textContent).toContain('净值日 2026-10-02')
})

it('hides saved NAV values when prices are missing', () => {
  render([makeRow({
    settled_total: 108_000,
    settled_pnl_pct: 8,
    settled_nav_status: 'missing_prices',
  })])

  const row = container.querySelector('tbody tr')!
  expect(row.textContent).toContain('行情缺失待核对')
  expect(row.textContent).not.toContain('+8.00%')
  expect(row.textContent).not.toContain('¥108,000.00')
})

it('shows only complete settled NAV values', () => {
  render([makeRow()])

  const row = container.querySelector('tbody tr')!
  expect(row.textContent).toContain('+8.00%')
  expect(row.textContent).toContain('¥108,000.00')
})

it('sorts by settled drawdown and filters by asset type', () => {
  render([
    makeRow({ account: 'stock-high-dd', strategy_id: 'alpha', asset_type: 'stock', settled_pnl_pct: 4, settled_max_drawdown: 15, max_drawdown: 1, win_rate: 75 }),
    makeRow({ account: 'etf-mid-dd', strategy_id: 'beta', asset_type: 'etf', settled_pnl_pct: 10, settled_max_drawdown: 10, max_drawdown: 99, win_rate: 40 }),
    makeRow({ account: 'stock-low-dd', strategy_id: 'gamma', asset_type: 'stock', settled_pnl_pct: null, pnl_pct: 90, settled_max_drawdown: null, max_drawdown: 3, rounds: 0, wins: 0, win_rate: 100 }),
  ])

  expect(accountOrder()).toEqual(['Beta', 'Alpha', 'Gamma'])
  click(container.querySelector<HTMLButtonElement>('button[aria-label="按胜率排序"]')!)
  expect(accountOrder()).toEqual(['Alpha', 'Beta', 'Gamma'])

  click(container.querySelector<HTMLButtonElement>('button[aria-label="按最大回撤排序"]')!)
  expect(accountOrder()).toEqual(['Beta', 'Alpha', 'Gamma'])
  const drawdowns = Array.from(container.querySelectorAll('tbody tr')).map(row => row.children[7]?.textContent)
  expect(drawdowns).toEqual(['-10.00%', '-15.00%', '—'])

  click(container.querySelector<HTMLButtonElement>('button[aria-label="筛选ETF策略模拟仓"]')!)
  expect(accountOrder()).toEqual(['Beta'])
})

it('allows pausing an active account but blocks enabling a frozen account', () => {
  const onToggle = vi.fn()
  render([
    makeRow({ account: 'active-paper', strategy_id: 'alpha', auto_enabled: true }),
    makeRow({ account: 'frozen-paper', strategy_id: 'beta', auto_enabled: false, status: 'frozen' }),
  ], onToggle)

  const pause = container.querySelector<HTMLButtonElement>('button[aria-label="暂停 Alpha 自动跟单"]')!
  const enableFrozen = container.querySelector<HTMLButtonElement>('button[aria-label="启用 Beta 自动跟单"]')!
  expect(enableFrozen.disabled).toBe(true)

  click(pause)
  click(enableFrozen)

  expect(onToggle).toHaveBeenCalledTimes(1)
  expect(onToggle).toHaveBeenCalledWith('active-paper', false)
})

it('expands the sample, holdings, and fee explanations', () => {
  render([makeRow()])

  click(container.querySelector<HTMLButtonElement>('button[aria-label="展开Alpha统计口径"]')!)

  expect(container.textContent).toContain('5 个 FIFO 配对的已平仓回合')
  expect(container.textContent).toContain('当前持仓标的 2 个')
  expect(container.textContent).toContain('只有行情完整的已保存收盘净值可用于累计收益')
  expect(container.textContent).toContain('佣金')
})
