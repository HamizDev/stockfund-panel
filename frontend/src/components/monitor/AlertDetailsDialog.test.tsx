// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AlertEvent } from '@/lib/api'
import { AlertDetailsDialog } from './AlertDetailsDialog'

let host: HTMLDivElement
let root: Root
const onClose = vi.fn()
const onPreview = vi.fn()
const signalLabel = (field: string) => ({
  ma_cross: '均线金叉',
  rsi_low: 'RSI 超卖',
}[field] ?? field)

function render(event: AlertEvent) {
  act(() => root.render(
    <AlertDetailsDialog
      event={event}
      sourceLabel="自定义"
      signalLabel={signalLabel}
      onClose={onClose}
      onPreview={onPreview}
    />,
  ))
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  onClose.mockReset()
  onPreview.mockReset()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const baseEvent: AlertEvent = {
  ts: Date.parse('2026-10-09T01:23:45.000Z'),
  source: 'custom',
  type: 'signal',
  symbol: '600000.SH',
  name: '浦发银行',
  message: '均线金叉触发',
  severity: 'warning',
}

describe('AlertDetailsDialog', () => {
  it('shows the complete message and trigger metadata with Beijing time', () => {
    const fullMessage = `完整消息开头 ${'监控条件与触发背景 '.repeat(100)}完整消息结尾`
    render({
      ...baseEvent,
      rule_name: '趋势提醒',
      strategy_id: 'strategy-42',
      message: fullMessage,
      price: 12.34,
      change_pct: 0.0123,
      signals: ['ma_cross'],
    })

    expect(host.textContent).toContain(fullMessage)
    expect(host.textContent).toContain('2026-10-09 09:23:45')
    expect(host.textContent).toContain('自定义')
    expect(host.textContent).toContain('关注')
    expect(host.textContent).toContain('趋势提醒')
    expect(host.textContent).toContain('strategy-42')
    const symbolRow = Array.from(host.querySelectorAll('dt')).find(row => row.textContent === '标的')?.parentElement
    expect(symbolRow?.textContent).toContain('浦发银行')
    expect(symbolRow?.textContent).toContain('600000.SH')
    expect(host.textContent).toContain('12.34')
    expect(host.textContent).toContain('+1.23%')
    expect(host.textContent).toContain('均线金叉')
  })

  it('labels missing message, price, change and invalid time as unrecorded', () => {
    const event = {
      ...baseEvent,
      ts: Number.NaN,
      message: undefined,
      price: null,
      change_pct: undefined,
    } as unknown as AlertEvent
    render(event)

    expect(host.textContent).toContain('未记录')
    expect(host.textContent).not.toContain('0.00')
    expect(host.textContent).not.toContain('0.00%')
  })

  it('localizes known severity values and keeps unknown values readable', () => {
    for (const [severity, expected] of [
      ['info', '提示'],
      ['warn', '关注'],
      ['warning', '关注'],
      ['critical', '重要'],
      ['custom-level', 'custom-level'],
    ]) {
      render({ ...baseEvent, severity })
      expect(host.textContent).toContain(expected)
    }
  })

  it('shows recorded conditions with localized field labels and explicit AND/OR logic', () => {
    render({
      ...baseEvent,
      conditions: [
        { field: 'ma_cross', op: 'truth' },
        { field: 'rsi_low', op: '>', value: 30 },
      ],
      logic: 'or',
    })

    expect(host.textContent).toContain('规则条件（触发时记录）')
    expect(host.textContent).toContain('满足任一条件（或）')
    expect(host.textContent).toContain('均线金叉')
    expect(host.textContent).toContain('RSI 超卖')
    expect(host.textContent).toContain('> 30')
    expect(host.textContent).not.toContain('全部命中')
  })

  it('passes the original event to preview for a single symbol', () => {
    render(baseEvent)
    const button = Array.from(host.querySelectorAll('button')).find(el => el.textContent === '查看标的日K')
    expect(button).toBeDefined()

    act(() => button!.click())

    expect(onPreview).toHaveBeenCalledWith(baseEvent)
  })

  it('lists every batch symbol and previews only the selected member without batch signals', () => {
    const event = {
      ...baseEvent,
      symbol: undefined,
      signals: ['ma_cross'],
      message: '共有 2 个标的满足策略条件',
      related_symbols: [
        { symbol: '510300.SH', name: '沪深300ETF' },
        { symbol: '159915.SZ', name: '创业板ETF' },
      ],
    } as AlertEvent
    render(event)

    const buttons = Array.from(host.querySelectorAll('button'))
    expect(buttons.some(button => button.textContent?.includes('510300.SH'))).toBe(true)
    expect(buttons.some(button => button.textContent?.includes('159915.SZ'))).toBe(true)
    expect(host.textContent).not.toContain('均线金叉')

    const selected = buttons.find(button => button.textContent?.includes('159915.SZ'))
    expect(selected).toBeDefined()
    act(() => selected!.click())

    expect(onPreview).toHaveBeenCalledWith({
      ...event,
      symbol: '159915.SZ',
      name: '创业板ETF',
      price: null,
      change_pct: null,
      signals: undefined,
    })
  })

  it('explains that a legacy strategy batch has no saved symbol without guessing one', () => {
    render({
      ...baseEvent,
      source: 'strategy',
      type: 'buy_signal',
      symbol: undefined,
      strategy_id: undefined,
      message: '策略批次命中 3 个标的',
      related_symbols: undefined,
    } as AlertEvent)

    expect(host.textContent).toContain('这条旧记录未保存标的代码，保留原始消息供查看。')
    expect(Array.from(host.querySelectorAll('button')).some(button => button.textContent === '查看标的日K')).toBe(false)
    expect(host.textContent).toContain('策略批次命中 3 个标的')
  })

  it('closes from the close button and Escape key', () => {
    render(baseEvent)
    const closeButton = host.querySelector<HTMLButtonElement>('button[aria-label="关闭详情"]')
    expect(closeButton).not.toBeNull()

    act(() => closeButton!.click())
    expect(onClose).toHaveBeenCalledTimes(1)

    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
