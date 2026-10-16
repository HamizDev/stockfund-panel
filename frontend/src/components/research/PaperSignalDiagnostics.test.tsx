// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { PaperCompareRow } from '@/lib/api'
import { PaperSignalDiagnostics } from './PaperSignalDiagnostics'

const fixtures = vi.hoisted(() => ({
  instrumentNames: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  api: { instrumentNames: fixtures.instrumentNames },
}))

type ShortTerm = NonNullable<PaperCompareRow['shortterm']>
type NamesResponse = { names: Record<string, string> }

function makeShortTerm(
  symbols: ShortTerm['symbols'] = {},
  overrides: Partial<ShortTerm> = {},
): ShortTerm {
  return {
    status: '自动短线观察中',
    last_check: '2026-10-15T10:01:00',
    symbols,
    t_rounds: [],
    ...overrides,
  }
}

function makeRow(overrides: Partial<PaperCompareRow> = {}): PaperCompareRow {
  return {
    account: 'etf-shortterm-test',
    name: 'ETF 短线测试账户',
    status: 'active',
    auto_enabled: true,
    shortterm: makeShortTerm(),
    ...overrides,
  } as PaperCompareRow
}

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixtures.instrumentNames.mockReset()
  fixtures.instrumentNames.mockImplementation(async (symbols: string[]) => ({
    names: Object.fromEntries(symbols.map(symbol => [symbol, 'ETF ' + symbol])),
  }))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: {
    retry: false, staleTime: Infinity, gcTime: Infinity,
  } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
})

async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

async function renderRow(row: PaperCompareRow) {
  await act(async () => root.render(
    <QueryClientProvider client={client}><PaperSignalDiagnostics row={row} /></QueryClientProvider>,
  ))
  await settle()
}

function pageText(): string {
  return host.textContent ?? ''
}

it('keeps ETF codes visible while the shared name batch is loading, then shows resolved names', async () => {
  let resolveNames!: (value: NamesResponse) => void
  fixtures.instrumentNames.mockReturnValue(new Promise<NamesResponse>(resolve => { resolveNames = resolve }))
  await renderRow(makeRow({
    shortterm: makeShortTerm({
      '510300': { reason: '等待实时行情' },
      '510050': { reason: '等待实时行情' },
    }),
  }))

  expect(pageText()).toContain('名称读取中…')
  expect(pageText()).toContain('510300')
  expect(fixtures.instrumentNames).toHaveBeenCalledTimes(1)
  expect(fixtures.instrumentNames.mock.calls[0]?.[0]).toEqual(['510050', '510300'])

  await act(async () => resolveNames({ names: { '510300': '沪深 300 ETF', '510050': '上证 50 ETF' } }))
  await settle()
  expect(pageText()).toContain('沪深 300 ETF')
  expect(pageText()).toContain('上证 50 ETF')
})

it('shows an empty state and unknown check time without requesting names for an empty record', async () => {
  await renderRow(makeRow({ shortterm: makeShortTerm({}, { last_check: undefined }) }))

  expect(pageText()).toContain('暂无短线候选观测')
  expect(pageText()).toContain('检查时间：未知')
  expect(fixtures.instrumentNames).not.toHaveBeenCalled()
})

it('surfaces a damaged runtime state and marks frozen observations as historical', async () => {
  await renderRow(makeRow({
    status: 'frozen',
    shortterm: makeShortTerm({
      '510300': {
        reason: '前日趋势已满足，等待闭合分钟回升',
        checked_at: '2026-10-15T09:55:00',
      },
    }, {
      state_error: '状态文件无法读取',
      status: '状态文件无法读取 · 本账户停止模拟',
      last_check: '2026-10-15T10:01:00',
    }),
  }))

  expect(host.querySelector('[role="alert"]')?.textContent).toContain('状态文件无法读取')
  expect(pageText()).toContain('账户已冻结')
  expect(pageText()).toContain('以下候选理由来自最近一次观测')
  expect(pageText()).toContain('旧观测 · 不是当前信号')
  expect(pageText()).toContain('2026-10-15 09:55:00')
})

it('summarizes reason categories and keeps unrecognized reasons visible as unknown', async () => {
  await renderRow(makeRow({
    shortterm: makeShortTerm({
      '510300': { reason: '等待30根闭合分钟' },
      '510500': { reason: '回归空间不足费用门槛' },
      '510800': { reason: '等待闭合分钟回升' },
      '511000': { reason: '没有可卖的部分底仓，等待可卖份额' },
      '511100': { reason: '等待后续有效行情撮合' },
      '511200': { reason: '此原因尚未配置分类' },
    }),
  }))

  expect(pageText()).toContain('数据等待 1')
  expect(pageText()).toContain('费用门槛 1')
  expect(pageText()).toContain('信号等待 1')
  expect(pageText()).toContain('持仓与资金 1')
  expect(pageText()).toContain('已有订单 1')
  expect(pageText()).toContain('未知原因 1')
  expect(pageText()).toContain('此原因尚未配置分类')
})

it('formats structured ratios as percentages and omits fields that are not supplied', async () => {
  await renderRow(makeRow({
    status: 'active',
    auto_enabled: false,
    shortterm: makeShortTerm({
      '510300': {
        reason: '等待闭合分钟回升',
        checked_at: '2026-10-15T09:59:00',
        diagnostic: {
          quote_price: 1.234,
          mean_30m: 1.2,
          range_ratio: 0.01,
          reversion_ratio: 0.005,
          spread_ratio: -0.002,
          required_ratio: 0.003,
          roundtrip_fee: 10,
          slippage_ratio: 0.001,
          buffer_ratio: 0.002,
          qty: 100,
          available_qty: 50,
          held_qty: 200,
          bar_time: '2026-10-15T09:58:00',
        },
      },
    }),
  }))

  expect(pageText()).toContain('近 30 分钟量均价')
  expect(pageText()).toContain('1.00%')
  expect(pageText()).toContain('0.50%')
  expect(pageText()).toContain('费用门槛')
  expect(pageText()).toContain('0.30%')
  expect(pageText()).toContain('预计双边佣金')
  expect(pageText()).toContain('¥10.00')
  expect(pageText()).not.toContain('1000.00%')
  expect(pageText()).toContain('分钟数据时间')
  expect(pageText()).toContain('旧观测 · 不是当前信号')
})

it('shows only supplied structured values and keeps missing timestamps explicit', async () => {
  await renderRow(makeRow({
    shortterm: makeShortTerm({
      '510300': {
        reason: '原因文字完整可读',
        diagnostic: {},
      },
    }, { last_check: undefined }),
  }))

  expect(pageText()).toContain('观测时间未知，不能确认当前信号')
  expect(pageText()).toContain('原因文字完整可读')
  expect(pageText()).not.toContain('近 30 分钟量均价')
  expect(pageText()).not.toContain('NaN')
  expect(pageText()).not.toContain('undefined')
})

it('limits the collapsed list to six candidates and expands the remaining rows', async () => {
  const symbols = Object.fromEntries(Array.from({ length: 7 }, (_, index) => {
    const code = '51030' + index
    return [code, { reason: '等待实时行情' }]
  }))
  await renderRow(makeRow({ shortterm: makeShortTerm(symbols) }))

  expect(host.querySelectorAll('article')).toHaveLength(6)
  const expandButton = Array.from(host.querySelectorAll('button'))
    .find(button => button.textContent?.includes('展开其余 1 个候选'))
  expect(expandButton).toBeDefined()
  await act(async () => expandButton?.click())
  expect(host.querySelectorAll('article')).toHaveLength(7)
  expect(expandButton?.getAttribute('aria-expanded')).toBe('true')
})

it('marks candidates as recent observations while the loop waits for trading or is paused', async () => {
  const symbols = { '510300': { reason: '等待后续闭合分钟回升' } }
  await renderRow(makeRow({
    shortterm: makeShortTerm(symbols, { status: '等待行情服务/交易时段' }),
  }))
  expect(pageText()).toContain('运行状态：等待行情服务/交易时段')
  expect(pageText()).toContain('以下候选理由来自最近一次观测')

  await renderRow(makeRow({
    auto_enabled: false,
    shortterm: makeShortTerm(symbols, { status: '暂停新信号' }),
  }))
  expect(pageText()).toContain('账户：策略已暂停')
  expect(pageText()).toContain('以下候选理由来自最近一次观测')
})
