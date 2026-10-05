// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { HoldingsDailyReviewScope, HoldingsDailyReviewState } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { HoldingsDailyReview, holdingsReviewSignature } from './HoldingsDailyReview'

const mocks = vi.hoisted(() => ({
  get: vi.fn<(scope: HoldingsDailyReviewScope) => Promise<HoldingsDailyReviewState>>(),
  start: vi.fn<(scope: HoldingsDailyReviewScope, force: boolean) => Promise<HoldingsDailyReviewState>>(),
}))

vi.mock('@/lib/api', () => ({
  api: {
    holdingsDailyReview: mocks.get,
    startHoldingsDailyReview: mocks.start,
  },
}))

let host: HTMLDivElement
let root: Root
let client: QueryClient

function state(
  status: HoldingsDailyReviewState['status'],
  today = '2026-10-05',
  overrides: Partial<HoldingsDailyReviewState> = {},
): HoldingsDailyReviewState {
  return {
    status,
    today,
    holdings_count: 2,
    holdings_changed: false,
    model_changed: false,
    report: null,
    ...overrides,
  }
}

const completeState = (date = '2026-10-05'): HoldingsDailyReviewState => state('complete', date, {
  report: {
    report_date: date,
    generated_at_ms: 1791192600000,
    model: 'gpt-6.1-sol',
    content: '## 持仓观察\n按条件观察，不自动交易。',
    observations: [{ symbol: '600000.SH', name: '浦发银行', asset_type: 'stock', data_date: date }],
    warnings: [],
  },
})

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  mocks.get.mockReset()
  mocks.start.mockReset()
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

async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

function render(scopes: HoldingsDailyReviewScope[] = ['lots'], signature = 'holdings-a') {
  act(() => root.render(
    <QueryClientProvider client={client}>
      {scopes.map(scope => <HoldingsDailyReview key={scope} scope={scope} holdingSignature={signature} loaded />)}
    </QueryClientProvider>,
  ))
}

describe('HoldingsDailyReview', () => {
  it('does not query or charge before holdings finish loading', () => {
    act(() => root.render(
      <QueryClientProvider client={client}>
        <HoldingsDailyReview scope="lots" holdingSignature="holdings-a" loaded={false} />
      </QueryClientProvider>,
    ))

    expect(host.textContent).toContain('正在读取今日分析状态')
    expect(mocks.get).not.toHaveBeenCalled()
    expect(mocks.start).not.toHaveBeenCalled()
  })

  it('clearly handles empty holdings and missing AI configuration without starting generation', async () => {
    mocks.get.mockImplementation(async scope => scope === 'lots'
      ? state('empty', '2026-10-05', { holdings_count: 0 })
      : state('unconfigured', '2026-10-05', { holdings_count: 1 }))

    render(['lots', 'fund'])
    await settle()

    expect(host.textContent).toContain('当前没有已登记持仓，暂不生成日报。')
    expect(host.textContent).toContain('尚未配置 AI。请先到设置页启用 AI 模型')
    expect(mocks.start).not.toHaveBeenCalled()
  })

  it('provides a read retry when the status endpoint fails before any result exists', async () => {
    mocks.get.mockRejectedValue(new Error('status unavailable'))

    render()
    await settle()

    expect(host.textContent).toContain('日报状态读取失败')
    expect(host.textContent).toContain('重试读取')
    expect(mocks.start).not.toHaveBeenCalled()
  })

  it('automatically starts one report per independent holdings scope after a successful status read', async () => {
    mocks.get.mockImplementation(async scope => state('not_generated', '2026-10-05', {
      holdings_count: scope === 'lots' ? 3 : 2,
    }))
    mocks.start.mockImplementation(async scope => state('running', '2026-10-05', {
      holdings_count: scope === 'lots' ? 3 : 2,
    }))

    render(['lots', 'fund'])
    await settle()

    expect(mocks.get).toHaveBeenCalledTimes(2)
    expect(mocks.get).toHaveBeenCalledWith('lots')
    expect(mocks.get).toHaveBeenCalledWith('fund')
    expect(mocks.start).toHaveBeenCalledTimes(2)
    expect(mocks.start).toHaveBeenCalledWith('lots', false)
    expect(mocks.start).toHaveBeenCalledWith('fund', false)
    expect(host.textContent).toContain('股票 / ETF 持仓日报')
    expect(host.textContent).toContain('基金持仓日报')
    expect(host.textContent).toContain('正在生成今日持仓分析')
    expect(QK.holdingsDailyReview('lots', 'same')).not.toEqual(QK.holdingsDailyReview('fund', 'same'))
  })

  it('does not loop a failed same-day automatic request, but permits the next server day', async () => {
    const statuses = [
      state('not_generated', '2026-10-05'),
      state('not_generated', '2026-10-05'),
      state('not_generated', '2026-10-06'),
    ]
    mocks.get.mockImplementation(async () => statuses.shift() ?? state('not_generated', '2026-10-06'))
    mocks.start.mockImplementation(async () => state('failed', '2026-10-05', {
      report: { ...completeState().report!, content: '', error: '日报未完成，请检查后重试。' },
    }))

    render()
    await settle()
    expect(mocks.start).toHaveBeenCalledTimes(1)
    expect(host.textContent).toContain('日报未完成，请检查后重试。')

    await act(async () => { await client.refetchQueries({ queryKey: QK.holdingsDailyReview('lots', 'holdings-a') }) })
    await settle()
    expect(mocks.start).toHaveBeenCalledTimes(1)

    await act(async () => { await client.refetchQueries({ queryKey: QK.holdingsDailyReview('lots', 'holdings-a') }) })
    await settle()
    expect(mocks.start).toHaveBeenCalledTimes(2)
    expect(mocks.start).toHaveBeenLastCalledWith('lots', false)
  })

  it('polls a running report every few seconds and renders the completed report', async () => {
    let reads = 0
    mocks.get.mockImplementation(async () => {
      reads++
      return reads === 1 ? state('not_generated') : completeState()
    })
    mocks.start.mockResolvedValue(state('running'))

    render()
    await settle()
    expect(host.textContent).toContain('正在生成今日持仓分析')

    await act(async () => { await new Promise(resolve => setTimeout(resolve, 3200)) })
    await settle()

    expect(mocks.get.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(host.textContent).toContain('按条件观察，不自动交易。')
  }, 10000)

  it('shows the report, data dates and changed-input hints; manual regeneration is explicitly forced', async () => {
    mocks.get.mockResolvedValue(state('complete', '2026-10-05', {
      ...completeState(),
      report: completeState().report,
      holdings_changed: true,
      model_changed: true,
    }))
    mocks.start.mockResolvedValue(state('running'))

    render()
    await settle()

    expect(host.textContent).toContain('按条件观察，不自动交易。')
    expect(host.textContent).toContain('登记持仓在这份报告生成后有变化。')
    expect(host.textContent).toContain('当前 AI 模型与生成报告时不同。')
    expect(host.textContent).toContain('600000.SH')
    expect(host.textContent).toContain('重新生成（会调用 AI）')

    const button = Array.from(host.querySelectorAll('button')).find(item => item.textContent?.includes('重新生成'))
    expect(button).toBeDefined()
    await act(async () => button!.click())
    await settle()
    expect(mocks.start).toHaveBeenCalledWith('lots', true)
  })

  it('keeps the last successful report visible when a later status refresh fails', async () => {
    mocks.get
      .mockResolvedValueOnce(completeState())
      .mockRejectedValueOnce(new Error('temporary status failure'))

    render()
    await settle()
    expect(host.textContent).toContain('按条件观察，不自动交易。')

    await act(async () => { await client.refetchQueries({ queryKey: QK.holdingsDailyReview('lots', 'holdings-a') }) })
    await settle()

    expect(host.textContent).toContain('日报状态暂时刷新失败')
    expect(host.textContent).toContain('按条件观察，不自动交易。')
  })

  it('uses an opaque stable signature so row order does not disclose or change the query partition', () => {
    const rows = [{ symbol: '600000.SH', qty: 10 }, { symbol: '510300.SH', qty: 20 }]
    expect(holdingsReviewSignature(rows)).toBe(holdingsReviewSignature([...rows].reverse()))
    expect(holdingsReviewSignature(rows)).not.toContain('600000.SH')
    expect(holdingsReviewSignature(rows)).not.toBe(holdingsReviewSignature([{ ...rows[0], qty: 11 }, rows[1]]))
  })
})
