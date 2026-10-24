// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StockAnalysisDialog } from './StockAnalysisDialog'
import type { ActiveTask, HistoryReport } from '@/lib/stockAnalysisStore'

vi.mock('framer-motion', () => ({
  motion: {
    div: (props: any) => {
      const domProps = Object.fromEntries(Object.entries(props).filter(([key]) =>
        !['initial', 'animate', 'exit', 'transition'].includes(key)))
      return <div {...domProps}>{props.children}</div>
    },
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}))
vi.mock('@/lib/stockAnalysisStore', () => ({
  minimizeDialog: vi.fn(), closeDialog: vi.fn(), startAnalysis: vi.fn(),
}))

let root: Root
let host: HTMLDivElement

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

async function render(task: ActiveTask | HistoryReport, mode: 'active' | 'history' = 'history') {
  await act(async () => root.render(<StockAnalysisDialog task={task} mode={mode} minimized={false} />))
}

it('shows the recorded quote cutoff and marks provisional daily data', async () => {
  await render({
    id: 'fresh', symbol: '600000.SH', name: '浦发银行', focus: '', content: '报告内容',
    as_of: '2026-10-22', daily_as_of: '2026-10-21',
    intraday_as_of: '2026-10-22T11:30:00+08:00', is_provisional: true,
    price_basis: 'shared_snapshot', created_at: '2026-10-22T15:00:00+08:00',
  })

  expect(host.querySelector('[data-testid="report-data-cutoff"]')?.textContent)
    .toBe('行情截至 2026-10-22 11:30 · 日K截至 2026-10-21 · 含未收盘K')
})

it('labels an old report as having no recorded cutoff instead of using its creation time', async () => {
  await render({
    id: 'legacy', symbol: '600000.SH', name: '浦发银行', focus: '', content: '旧报告',
    created_at: '2026-10-22T15:00:00+08:00',
  })

  expect(host.querySelector('[data-testid="report-data-cutoff"]')?.textContent).toBe('数据截至时间未记录')
  expect(host.querySelector('[data-testid="report-data-cutoff"]')?.textContent).not.toContain('15:00')
})

it('shows request minute separately from actual market cutoff for saved and active reports', async () => {
  await render({
    id: 'timed', symbol: '515880.SH', name: '通信ETF', focus: '', content: '条件计划',
    created_at: '2026-10-23T14:18:00+08:00', requested_at: '2026-10-23T14:16:42+08:00',
    as_of: '2026-10-23', intraday_as_of: '2026-10-23T14:16:00+08:00', is_provisional: true,
  })
  expect(host.querySelector('[data-testid="report-data-cutoff"]')?.textContent)
    .toBe('提问 2026-10-23 14:16 · 行情截至 2026-10-23 14:16 · 含未收盘K')
  expect(host.querySelector('[data-testid="report-data-cutoff"]')?.textContent).not.toContain('14:18')
})

it('describes the shared quote snapshot in the active analysis footer', async () => {
  await render({
    id: 'active', symbol: '600000.SH', name: '浦发银行', focus: '', phase: 'done',
    content: '新报告', error: '', createdAt: Date.now(),
    meta: {
      as_of: '2026-10-22', intraday_as_of: '2026-10-22T11:30:00+08:00',
      is_provisional: true, price_basis: 'shared_snapshot',
    },
  }, 'active')

  expect(host.textContent).toContain('个股分析页共用行情快照')
  expect(host.textContent).toContain('含未收盘K')
})

it('keeps data freshness failures separate from model credential configuration', async () => {
  await render({
    id: 'stale', symbol: '515880.SH', name: '通信ETF', focus: '', phase: 'error',
    content: '', error: '最新分钟数据尚未到14:16,本次AI分析停止', createdAt: Date.now(), meta: null,
  }, 'active')
  expect(host.textContent).toContain('14:16')
  expect(host.textContent).not.toContain('去配置 AI')
})
