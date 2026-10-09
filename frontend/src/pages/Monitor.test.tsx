// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type AlertEvent } from '@/lib/api'
import { AlertsList } from './Monitor'

vi.mock('@/lib/useCustomSignalNames', () => ({ useCustomSignalNames: () => ({}) }))
vi.mock('@/components/monitor/RuleEditor', () => ({ RuleEditor: () => null }))
vi.mock('@/components/DimensionMembersDialog', () => ({ DimensionMembersDialog: () => null }))
vi.mock('@/components/StockPreviewDialog', () => ({
  StockPreviewDialog: ({ symbol, triggerInfo, navList, onNavigate, onClose }: any) => symbol ? (
    <div data-testid="stock-preview">
      <span>{symbol}</span>
      <span data-testid="trigger-info">{triggerInfo ? triggerInfo.message : '无单股触发快照'}</span>
      {navList.map((item: any) => <button key={item.symbol} onClick={() => onNavigate(item.symbol, item.name)}>切换 {item.symbol}</button>)}
      <button onClick={onClose}>关闭日K</button>
    </div>
  ) : null,
}))

let host: HTMLDivElement
let root: Root
let client: QueryClient
const event: AlertEvent = {
  ts: 1791504000000, source: 'signal', type: 'signal', symbol: '600000.SH',
  name: '浦发银行', message: '完整触发消息', price: 12, change_pct: 0.01, signals: ['signal_buy'],
}

function Location() {
  return <span data-testid="location">{useLocation().pathname}{useLocation().search}</span>
}

function render(events: AlertEvent[], isLoading = false) {
  const query = { data: { alerts: events }, isLoading } as Parameters<typeof AlertsList>[0]['alertsQuery']
  act(() => root.render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AlertsList alertsQuery={query} confirmClear={false} setConfirmClear={() => {}} total={events.length} enterTs={Infinity} monitorExtFields={{ concept: null, industry: null }} />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  ))
}

function clickButton(label: string) {
  const button = Array.from(host.querySelectorAll('button')).find(el => el.textContent === label)
  expect(button, label).toBeDefined()
  act(() => button!.click())
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  client.clear()
  vi.restoreAllMocks()
})

describe('monitor message details', () => {
  it('opens a stable trigger snapshot from the message and the explicit detail button', () => {
    render([event])
    act(() => host.querySelector<HTMLElement>('[role="article"]')!.click())
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain('完整触发消息')
    render([{ ...event, message: '新一轮数据' }])
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain('完整触发消息')
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="关闭详情"]')!.click())
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="查看消息详情"]')!.click())
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain('新一轮数据')
  })

  it('keeps stock-name preview and two-step deletion separate from opening details', () => {
    const remove = vi.spyOn(api, 'alertDelete').mockResolvedValue({ ok: true })
    render([event, { ...event, ts: event.ts - 1, asset_type: 'index', symbol: '000688.SH' }])
    act(() => host.querySelector<HTMLButtonElement>('[title="点击查看日K"]')!.click())
    expect(host.querySelector('[data-testid="stock-preview"]')?.textContent).toContain(event.symbol)
    expect(host.querySelector('[role="dialog"]')).toBeNull()
    expect(host.textContent).not.toContain('切换 000688.SH')
    clickButton('关闭日K')
    act(() => host.querySelector<HTMLButtonElement>('[title="删除"]')!.click())
    expect(host.querySelector('[title="再次点击确认删除"]')).not.toBeNull()
    expect(host.querySelector('[role="dialog"]')).toBeNull()
    expect(remove).not.toHaveBeenCalled()
  })

  it('previews and navigates batch members without inventing individual trigger data', () => {
    render([{ ...event, symbol: '', source: 'strategy', type: 'buy_signal', asset_type: 'etf',
      related_symbols: [{ symbol: '510300.SH', name: '沪深300ETF' }, { symbol: '159915.SZ', name: '创业板ETF' }],
    }])
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="查看消息详情"]')!.click())
    const member = Array.from(host.querySelectorAll('button')).find(el => el.textContent?.startsWith('沪深300ETF'))!
    act(() => member.click())
    expect(host.querySelector('[role="dialog"]')).toBeNull()
    expect(host.querySelector('[data-testid="stock-preview"]')?.textContent).toContain('510300.SH')
    expect(host.querySelector('[data-testid="trigger-info"]')?.textContent).toBe('无单股触发快照')
    clickButton('切换 159915.SZ')
    expect(host.querySelector('[data-testid="stock-preview"]')?.textContent).toContain('159915.SZ')
  })

  it('opens index details on the index page instead of the stock preview', () => {
    render([{ ...event, asset_type: 'index', symbol: '000688.SH' }])
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="查看消息详情"]')!.click())
    clickButton('查看标的日K')
    expect(host.querySelector('[data-testid="location"]')?.textContent).toBe('/indices?symbol=000688.SH')
    expect(host.querySelector('[data-testid="stock-preview"]')).toBeNull()
  })

  it('handles empty and loading lists without showing stale details', () => {
    render([])
    expect(host.textContent).toContain('暂无触发记录')
    expect(host.querySelector('[aria-label="查看消息详情"]')).toBeNull()
    render([], true)
    expect(host.textContent).not.toContain('暂无触发记录')
  })
})
