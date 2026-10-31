// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ResearchReviewPanel } from './ResearchReviewPanel'
import type {
  ResearchDecisionDetail,
  ResearchDecisionList,
  ResearchOutcome,
} from '@/lib/researchTypes'

const mocks = vi.hoisted(() => ({
  researchDecisions: vi.fn(),
  researchDecision: vi.fn(),
  researchDecisionRefresh: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ api: mocks }))
vi.mock('@/lib/queryKeys', () => ({
  QK: {
    researchDecisions: ['research-decisions'],
    researchDecision: (id: string) => ['research-decision', id],
  },
}))

const row = {
  id: 'decision-1',
  symbol: '600000.SH',
  name: '浦发银行',
  created_at: '2026-10-20T10:00:00+08:00',
  horizon_sessions: 3,
  direction: 'up',
  reference_price: 10,
  status: 'pending',
  target_date: null,
  price_return: null,
  hit: null,
}
const secondRow = {
  ...row,
  id: 'decision-2',
  symbol: '000001.SZ',
  name: '平安银行',
}

const list: ResearchDecisionList = {
  items: [row, secondRow],
  total: 14,
  recent_limit: 200,
  statistics: {
    evaluated: 3,
    direction_hit_rate: 2 / 3,
    always_up_hit_rate: 1 / 3,
    always_down_hit_rate: 2 / 3,
  },
  limits: '统计范围为最近已评价研究。',
}

const detail: ResearchDecisionDetail = {
  record: {
    id: row.id,
    symbol: row.symbol,
    name: row.name,
    created_at: row.created_at,
    requested_at: '2026-10-20T09:58:00+08:00',
    horizon_sessions: 3,
    direction: 'up',
    reference_price: 10,
    snapshot: { source: 'saved' },
    portfolio: {
      paper_accounts_state: 'available',
      paper_accounts: [
        {
          id: 'paper-7', state: 'held', qty: 12, available_qty: 8, avg_cost: 9.75, cash: 1250,
          fees: { commission_pct: 0.0003, stamp_tax_pct: 0.0005, slippage_bps: 5, min_commission: 5 },
        },
        {
          id: 'paper-8', state: 'flat', qty: 0, available_qty: 0, avg_cost: null, cash: 500,
          fees: { commission_pct: 0.00005, stamp_tax_pct: 0.0005, slippage_bps: 2, min_commission: 2 },
        },
      ],
      broker: { state: 'unknown', source: 'not_connected', cash: null, available_qty: null },
      registered_lots: { state: 'registered', items: [{ id: 'manual-1', qty: 3500, cost_price: .531 }] },
    },
    review: { note: 'saved review' },
    score_definition: '提问日之后第 3 个实际交易日的原始价格变化。',
  },
  outcome: null,
}

const completed: ResearchOutcome = {
  id: row.id,
  status: 'complete',
  target_date: '2026-10-23',
  actual_price: 11.25,
  price_return: 0.125,
  hit: true,
  message: '已对照目标原始收盘价；未计分红、费用和成交约束。',
  checked_at: '2026-10-24T08:00:00+08:00',
}

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  mocks.researchDecisions.mockReset().mockResolvedValue(list)
  mocks.researchDecision.mockReset().mockImplementation(async (id: string) => id === secondRow.id ? {
    ...detail,
    record: { ...detail.record, id: secondRow.id, symbol: secondRow.symbol, name: secondRow.name },
  } : detail)
  mocks.researchDecisionRefresh.mockReset().mockImplementation(async (id: string) => ({ ...completed, id }))
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
})

async function settle() {
  for (let index = 0; index < 5; index++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

async function render(props: { recordId?: string | null; compact?: boolean } = {}) {
  await act(async () => root.render(
    <QueryClientProvider client={client}><ResearchReviewPanel {...props} /></QueryClientProvider>,
  ))
  await settle()
}

async function click(button: HTMLButtonElement) {
  await act(async () => button.click())
  await settle()
}

it('loads the read-only list and only checks a selected record after a click', async () => {
  await render()

  expect(mocks.researchDecisions).toHaveBeenCalledTimes(1)
  expect(mocks.researchDecision).toHaveBeenCalledTimes(1)
  expect(mocks.researchDecision.mock.calls[0][0]).toBe('decision-1')
  expect(mocks.researchDecisionRefresh).not.toHaveBeenCalled()
  expect(host.textContent).toContain('记录总数')
  expect(host.textContent).toContain('14')
  expect(host.textContent).toContain('方向命中率')
  expect(host.textContent).toContain('始终看涨基线')
  expect(host.textContent).toContain('始终看跌基线')

  expect(host.textContent).toContain('浦发银行')
  expect(host.textContent).toContain('600000.SH')
  await click(host.querySelector<HTMLButtonElement>('[aria-label="查看 平安银行 研究复盘详情"]')!)
  expect(mocks.researchDecision).toHaveBeenCalledTimes(2)
  expect(mocks.researchDecision.mock.calls[1][0]).toBe('decision-2')
  expect(mocks.researchDecisionRefresh).not.toHaveBeenCalled()
  expect(host.textContent).toContain('平安银行 · 研究复盘')
  expect(host.textContent).toContain('原始参考价')
  expect(host.textContent).toContain('目标原始收盘价')
  expect(host.textContent).toContain('费用后交易收益')

  await click([...host.querySelectorAll('button')].find(button => button.textContent === '刷新列表')!)
  expect(host.textContent).toContain('平安银行 · 研究复盘')
  expect(mocks.researchDecisionRefresh).not.toHaveBeenCalled()

  const check = [...host.querySelectorAll('button')].find(button => button.textContent === '核对真实行情')!
  await click(check)
  expect(mocks.researchDecisionRefresh).toHaveBeenCalledTimes(1)
  expect(mocks.researchDecisionRefresh).toHaveBeenCalledWith('decision-2')
  expect(host.textContent).toContain('2026-10-23')
  expect(host.textContent).toContain('10.00')
  expect(host.textContent).toContain('11.25')
  expect(host.textContent).toContain('+1.25 (+12.50%)')
  expect(host.textContent).toContain('命中')
  expect(host.textContent).toContain(completed.message)
  expect(host.textContent).toContain('纸面账户 paper-7')
  expect(host.textContent).toContain('可用数量')
  expect(host.textContent).not.toContain('available_qty')
  expect(host.textContent).toContain('佣金率')
  expect(host.textContent).toContain('0.03%')
  expect(host.textContent).toContain('5 基点')
  const paper7 = host.querySelector<HTMLElement>('[data-paper-account-id="paper-7"]')!
  const paper8 = host.querySelector<HTMLElement>('[data-paper-account-id="paper-8"]')!
  expect(paper7.textContent).toContain('1250.00')
  expect(paper7.textContent).not.toContain('500.00')
  expect(paper8.textContent).toContain('500.00')
  expect(paper8.textContent).toContain('0.005%')
  expect(paper8.textContent).not.toContain('1250.00')
  expect(host.textContent).toContain('实盘券商')
  expect(host.textContent).toContain('未知（未连接）')
  expect(host.textContent).toContain('用户登记的参考批次')
  expect(host.textContent).toContain('登记数量 3500')
  expect(host.textContent).toContain('登记成本 0.531')
  expect(host.textContent).toContain('不与模拟账户合并')
  expect(host.textContent).toContain('不是下单许可')
})

it('does not list or backfill an old report without a record id in compact mode', async () => {
  await render({ compact: true, recordId: null })

  expect(host.textContent).toContain('未建档')
  expect(host.textContent).toContain('不会追补')
  expect(mocks.researchDecisions).not.toHaveBeenCalled()
  expect(mocks.researchDecision).not.toHaveBeenCalled()
  expect(mocks.researchDecisionRefresh).not.toHaveBeenCalled()
})

it('refreshes summary statistics after a successful outcome check', async () => {
  mocks.researchDecisions.mockResolvedValueOnce(list).mockResolvedValue({
    ...list, statistics: { ...list.statistics, evaluated: 4, direction_hit_rate: .75 },
  })
  await render()
  const check = [...host.querySelectorAll('button')].find(button => button.textContent === '核对真实行情')!
  await click(check)
  expect(mocks.researchDecisions.mock.calls.length).toBeGreaterThanOrEqual(2)
  expect(host.textContent).toContain('方向命中率 · 4 条')
})

it('compact mode queries only the requested record and keeps paper data separate from broker state', async () => {
  await render({ compact: true, recordId: 'decision-1' })

  expect(mocks.researchDecisions).not.toHaveBeenCalled()
  expect(mocks.researchDecision).toHaveBeenCalledTimes(1)
  expect(mocks.researchDecision.mock.calls[0][0]).toBe('decision-1')
  expect(mocks.researchDecisionRefresh).not.toHaveBeenCalled()
  expect(host.textContent).toContain('提问日之后第 3 个实际交易日')
  expect(host.textContent).toContain('纸面账户与实盘状态')
  expect(host.textContent).toContain('实盘券商')
  expect(host.textContent).toContain('未知（未连接）')
})

it('switches to a new record id without showing the previously loaded detail', async () => {
  await render({ compact: true, recordId: 'decision-1' })
  expect(host.textContent).toContain('浦发银行 · 研究复盘')

  await act(async () => root.render(
    <QueryClientProvider client={client}><ResearchReviewPanel compact recordId="decision-2" /></QueryClientProvider>,
  ))
  await settle()

  expect(host.textContent).toContain('平安银行 · 研究复盘')
  expect(host.textContent).not.toContain('浦发银行 · 研究复盘')
  expect(mocks.researchDecision.mock.calls.map(call => call[0])).toEqual(['decision-1', 'decision-2'])
  expect(mocks.researchDecisions).not.toHaveBeenCalled()
  expect(mocks.researchDecisionRefresh).not.toHaveBeenCalled()
})

it('shows an empty state without requesting a detail or outcome', async () => {
  mocks.researchDecisions.mockResolvedValueOnce({ ...list, items: [], total: 0 })
  await render()

  expect(host.textContent).toContain('暂无已建档的研究复盘记录')
  expect(mocks.researchDecision).not.toHaveBeenCalled()
  expect(mocks.researchDecisionRefresh).not.toHaveBeenCalled()
})

it('reports list and detail failures without issuing an outcome POST', async () => {
  mocks.researchDecisions.mockRejectedValueOnce(new Error('list offline'))
  await render()
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('list offline')
  expect(mocks.researchDecisionRefresh).not.toHaveBeenCalled()

  mocks.researchDecision.mockRejectedValueOnce(new Error('detail offline'))
  await act(async () => root.render(
    <QueryClientProvider client={client}><ResearchReviewPanel compact recordId="decision-1" /></QueryClientProvider>,
  ))
  await settle()
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('detail offline')
  expect(mocks.researchDecisionRefresh).not.toHaveBeenCalled()
})
