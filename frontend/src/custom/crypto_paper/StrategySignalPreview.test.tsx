// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StrategySignalPreview } from './StrategySignalPreview'
import { cryptoApi, type CryptoStrategyPreviewRequest } from './client'

vi.mock('./client', () => ({ cryptoApi: { strategyPreview: vi.fn() } }))
let host: HTMLDivElement
let root: Root
const body: CryptoStrategyPreviewRequest = {
  exchange: 'bitget', market: 'usdm', symbol: 'ETHUSDT', interval: '1h', leverage: 1, allocation_pct: 10,
  strategy_id: 'dmi_confirm', strategy_params: { ma_period: 55, adx_threshold: 23 },
}
const response = {
  strategy_id: 'dmi_confirm', strategy_params: body.strategy_params,
  analysis: { target: 'hold', reason: '等待确认', metrics: { adx_14: 18.5 }, conditions: { strength: false } },
  evidence: { symbol: 'ETHUSDT', interval: '1h', bars_count: 200, data_end_ms: 1800000000000,
    taker_fee_rate: '0.0006', spread_bps: 1.5 },
  method: '未计算收益和胜率', adaptation: '研究改编',
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  vi.mocked(cryptoApi.strategyPreview).mockReset()
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })
async function render(key = 'A', getRequest = () => body) {
  await act(async () => root.render(<StrategySignalPreview contextKey={key} getRequest={getRequest} />))
}
async function click() { await act(async () => host.querySelector('button')!.click()) }

it('requests only after click and labels observation, fees and non-performance correctly', async () => {
  vi.mocked(cryptoApi.strategyPreview).mockResolvedValue(response as never)
  await render()
  expect(cryptoApi.strategyPreview).not.toHaveBeenCalled()
  await click()
  expect(cryptoApi.strategyPreview).toHaveBeenCalledWith(body, expect.any(AbortSignal))
  expect(host.textContent).toContain('保持当前仓位 / 空仓继续等待')
  expect(host.textContent).toContain('0.0600% / 单边')
  expect(host.textContent).toContain('未计算收益和胜率')
})

it('clears real result state across A to B to A', async () => {
  vi.mocked(cryptoApi.strategyPreview).mockResolvedValue(response as never)
  await render(); await click()
  expect(host.textContent).toContain('收盘信号')
  await render('B'); await render('A')
  expect(host.textContent).not.toContain('收盘信号')
})

it('ignores a stale response after parameters change and aborts its request', async () => {
  let finish!: (value: never) => void
  vi.mocked(cryptoApi.strategyPreview).mockImplementation(() => new Promise(resolve => { finish = resolve }))
  await render(); await click()
  const signal = vi.mocked(cryptoApi.strategyPreview).mock.calls[0][1]!
  await render('B')
  expect(signal.aborted).toBe(true)
  await act(async () => finish(response as never))
  expect(host.textContent).not.toContain('收盘信号')
})

it('does not fetch when local parameters are invalid', async () => {
  await render('invalid', () => { throw new Error('参数超出范围') })
  await click()
  expect(host.querySelector('[role="alert"]')?.textContent).toBe('参数超出范围')
  expect(cryptoApi.strategyPreview).not.toHaveBeenCalled()
})

it('shows Chinese labels for Supertrend and BB/Aroon metrics and conditions', async () => {
  const result = {
    ...response,
    strategy_id: 'supertrend_pullback',
    analysis: {
      target: 'long', reason: '趋势翻向',
      metrics: { atr: 12.3, supertrend: 3012.5, ema: 3000, bb_upper: 3100, bb_middle: 3000, bb_lower: 2900, aroon_up: 95 },
      conditions: { trend_up: true, trend_flip: true, rebound_up: true, rebound_down: false,
        crossed_below_lower: false, crossed_below_upper: false, aroon_entry: true, aroon_exit: false },
    },
  }
  vi.mocked(cryptoApi.strategyPreview).mockResolvedValue(result as never)
  await render()
  await click()
  for (const label of ['ATR', 'Supertrend', 'EMA', '布林带上轨', '布林带中轨', '布林带下轨', 'AroonUp',
    'Supertrend 趋势向上', 'Supertrend 本根翻向', '回穿 EMA 向上', '回穿 EMA 向下',
    '收盘下穿布林带下轨', '收盘下穿布林带上轨', 'AroonUp 高于 90 入场门槛', 'AroonUp 低于 70 离场门槛']) {
    expect(host.textContent).toContain(label)
  }
})
