// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StrategyAccountsPanel } from './StrategyAccountsPanel'
import { cryptoApi } from './client'

vi.mock('./AiStrategyDraftPanel', () => ({
  AiStrategyDraftPanel: ({ onApply }: { onApply: (draft: unknown) => void }) => <button type="button" data-testid="apply-supertrend-draft" onClick={() => onApply({
    name: 'AI Supertrend', exchange: 'bitget', market: 'usdm', symbol: 'SOLUSDT', interval: '4h',
    strategy_id: 'supertrend_pullback', strategy_params: { atr_period: 14, atr_multiplier: 4, ema_period: 34 },
    leverage: 5, initial_cash: 2500, allocation_pct: 12, stop_loss_pct: 2.5, take_profit_pct: 5,
  })}>应用 AI Supertrend 草案</button>,
}))
vi.mock('./client', () => ({ cryptoApi: { strategies: vi.fn(), strategyAccounts: vi.fn(),
  createStrategyAccount: vi.fn(), strategyPreview: vi.fn() } }))
let host: HTMLDivElement
let root: Root
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  const model = { poll_seconds: 30, slippage_bps: 5, maintenance_margin_rate: '.005', liquidation_fee_rate: '.005' }
  vi.mocked(cryptoApi.strategies).mockResolvedValue({ model, strategies: [
    { id: 'ema_trend', name: 'EMA', description: 'old' },
    { id: 'dmi_confirm', name: 'DMI研究', description: 'DMI连续方向确认' },
    { id: 'squeeze_release', name: 'TTM研究', description: '收缩释放' },
    { id: 'supertrend_pullback', name: 'Supertrend 回踩', description: 'ST 与 EMA 回穿研究' },
    { id: 'bb_aroon', name: '布林带 Aroon', description: '布林带与 Aroon 研究' },
  ] })
  vi.mocked(cryptoApi.strategyAccounts).mockResolvedValue({ accounts: [], model, runtime: { running: true, poll_seconds: 30 } })
  vi.mocked(cryptoApi.createStrategyAccount).mockReset().mockRejectedValue(new Error('isolated capture'))
  vi.mocked(cryptoApi.strategyPreview).mockReset()
  await act(async () => root.render(<StrategyAccountsPanel />))
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })
async function choose(id: string) {
  const select = host.querySelector<HTMLSelectElement>('[aria-label="策略类型"]')!
  await act(async () => { select.value = id; select.dispatchEvent(new Event('change', { bubbles: true })) })
}
async function input(label: string, value: string) {
  const element = host.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
it('sends DMI parameters to the existing paused-account creation flow', async () => {
  await choose('dmi_confirm')
  await input('DMI 过滤均线周期', '66'); await input('ADX 强度门槛', '26')
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(cryptoApi.createStrategyAccount).toHaveBeenCalledWith(expect.objectContaining({
    strategy_id: 'dmi_confirm', strategy_params: { ma_period: 66, adx_threshold: 26 }, exchange: 'bitget',
  }))
  expect(host.textContent).toContain('创建暂停账户')
})
it('shows TTM bounds and rejects invalid preview parameters before any request', async () => {
  await choose('squeeze_release'); await input('收缩回看周期', '61')
  expect(host.querySelector('[aria-label="收缩回看周期"]')?.getAttribute('max')).toBe('60')
  const button = [...host.querySelectorAll('button')].find(item => item.textContent === '预览当前规则信号')!
  await act(async () => button.click())
  expect(host.textContent).toContain('收缩回看周期须为 10 至 60 的整数')
  expect(cryptoApi.strategyPreview).not.toHaveBeenCalled()
  expect(cryptoApi.createStrategyAccount).not.toHaveBeenCalled()
})

it('shows BB/Aroon defaults and configured integer bounds in the account form', async () => {
  await choose('bb_aroon')
  const bb = host.querySelector<HTMLInputElement>('[aria-label="布林带周期"]')!
  const aroon = host.querySelector<HTMLInputElement>('[aria-label="Aroon 周期"]')!
  expect([bb.value, bb.min, bb.max, bb.step]).toEqual(['20', '10', '60', '1'])
  expect([aroon.value, aroon.min, aroon.max, aroon.step]).toEqual(['100', '20', '120', '1'])
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(cryptoApi.createStrategyAccount).toHaveBeenCalledWith(expect.objectContaining({
    strategy_id: 'bb_aroon', strategy_params: { bb_period: 20, aroon_period: 100 },
  }))
})

it('applies AI Supertrend parameters and preserves them in manual account creation', async () => {
  await act(async () => host.querySelector<HTMLButtonElement>('[data-testid="apply-supertrend-draft"]')!.click())
  expect(host.querySelector<HTMLInputElement>('[aria-label="Supertrend ATR 周期"]')?.value).toBe('14')
  expect(host.querySelector<HTMLInputElement>('[aria-label="Supertrend ATR 倍数"]')?.value).toBe('4')
  expect(host.querySelector<HTMLInputElement>('[aria-label="趋势 EMA 周期"]')?.value).toBe('34')
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(cryptoApi.createStrategyAccount).toHaveBeenCalledWith(expect.objectContaining({
    name: 'AI Supertrend', symbol: 'SOLUSDT', interval: '4h', strategy_id: 'supertrend_pullback',
    strategy_params: { atr_period: 14, atr_multiplier: 4, ema_period: 34 },
  }))
})
