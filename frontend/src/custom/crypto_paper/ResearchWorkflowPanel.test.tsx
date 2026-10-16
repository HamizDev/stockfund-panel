// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ResearchWorkflowPanel } from './ResearchWorkflowPanel'
import { cryptoApi, type CryptoResearchBacktestRequest, type CryptoResearchBacktestRun, type CryptoResearchDecision, type CryptoStrategyAccount } from './client'

vi.mock('echarts', () => ({ init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })) }))
vi.mock('./client', () => ({ cryptoApi: {
  strategies: vi.fn(), researchHistory: vi.fn(), researchBacktest: vi.fn(), researchRun: vi.fn(),
  strategyAccounts: vi.fn(), researchAccountDecisions: vi.fn(), researchReviews: vi.fn(),
  createResearchReview: vi.fn(), researchReviewAI: vi.fn(),
} }))

const strategies = [
  { id: 'ema_trend' as const, name: 'EMA 趋势', description: '均线' },
  { id: 'supertrend_pullback' as const, name: 'Supertrend 回踩', description: '趋势' },
  { id: 'bb_aroon' as const, name: '布林带 Aroon', description: '回撤' },
]
const model = { maintenance_margin_rate: '0.005', liquidation_fee_rate: '0.005', slippage_bps: 5, poll_seconds: 30 }
function account(id: string, exchange: 'bitget' | 'binance' = 'bitget'): CryptoStrategyAccount {
  return {
    id, name: `账户 ${id}`, exchange, market: 'usdm', symbol: 'BTCUSDT', strategy_id: 'ema_trend', interval: '1h',
    leverage: 1, initial_cash: '10000', allocation_pct: 10, stop_loss_pct: 2, take_profit_pct: 4,
    enabled: false, status: 'paused', last_error: null, last_check_ms: null, last_signal: null, last_bar_time_ms: null,
    cash: '10000', equity: '10000', total_pnl: null, return_pct: null, max_drawdown_pct: null,
    fee_total: '0', funding_total: '0', liquidation_count: 0, trade_count: 0, positions: {},
  }
}
function config(): CryptoResearchBacktestRequest {
  return { strategy_id: 'ema_trend', strategy_params: { fast_period: 20, slow_period: 60 }, symbol: 'BTCUSDT', interval: '1h',
    bars: 500, initial_cash: 10000, allocation_pct: 10, stop_loss_pct: 2, take_profit_pct: 4 }
}
function run(body = config()): CryptoResearchBacktestRun {
  return {
    id: 'run-1', created_at_ms: 1800000000000, config: body,
    evidence: { bars_count: body.bars, data_start_ms: 1790000000000, data_end_ms: 1800000000000, exchange: 'bitget', fee_rate: '0.0006', slippage_bps: 5 },
    metrics: { return_pct: 2.5, max_drawdown_pct: 1.2, closed_trades: 2, win_rate_pct: 50, fees_paid: '2.1', ending_equity: '10250' },
    nav: [{ at_ms: 1790000000000, equity: '10000' }, { at_ms: 1800000000000, equity: '10250' }],
    trades: [{ signal_time_ms: 1799990000000, fill_time_ms: 1800000000000, action: 'open_long', price: '100', quantity: '1', fee: '0.06', realized_pnl: '0' }],
    warnings: ['资金费未计入'], method: '收盘信号在下一根 K 线开盘价模拟成交。',
  }
}
const report = {
  date: '2026-10-16', created_at_ms: 1800000000000,
  accounts: [{ id: 'account-1', name: '账户 account-1', checks: 12, actions: 2, blocked: 1, last_reason: '风险限制阻止开仓', trade_count: 3, equity: null }],
  summary: '今日复盘按去重决策和账本数据统计。', candidates: [{ symbol: 'BTCUSDT', reason: '样本条件满足' }], method: '每日固定统计；非 AI。',
}

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  vi.mocked(cryptoApi.strategies).mockReset().mockResolvedValue({ strategies, model })
  vi.mocked(cryptoApi.researchHistory).mockReset().mockResolvedValue({ runs: [] })
  vi.mocked(cryptoApi.researchBacktest).mockReset().mockImplementation(async body => run(body))
  vi.mocked(cryptoApi.researchRun).mockReset().mockImplementation(async id => ({ ...run(), id }))
  vi.mocked(cryptoApi.strategyAccounts).mockReset().mockResolvedValue({ accounts: [account('account-1'), account('account-2'), account('legacy', 'binance')], model, runtime: { running: false, poll_seconds: 30 } })
  vi.mocked(cryptoApi.researchAccountDecisions).mockReset().mockImplementation(async id => ({ decisions: [{
    at_ms: 1800000000000, bar_time_ms: null, target: 'long', status: 'blocked', reason: `风险限制 ${id}`,
    metrics: { atr: 12.5, unavailable: null }, conditions: { trend_up: true }, equity: null, trades_added: 0,
  }] as CryptoResearchDecision[] }))
  vi.mocked(cryptoApi.researchReviews).mockReset().mockResolvedValue({ reviews: [report] })
  vi.mocked(cryptoApi.createResearchReview).mockReset().mockResolvedValue({ review: report })
  vi.mocked(cryptoApi.researchReviewAI).mockReset().mockResolvedValue({ review: report, ai: { content: '按需生成的复盘解读', model: 'configured-model', created_at_ms: 1800000001000 } })
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })

async function settle() {
  for (let index = 0; index < 5; index++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}
async function render() { await act(async () => root.render(<ResearchWorkflowPanel />)); await settle() }
async function clickText(text: string) {
  const button = [...host.querySelectorAll('button')].find(item => item.textContent?.trim() === text)
  if (!button) throw new Error(`找不到按钮：${text}`)
  await act(async () => button.click())
}
async function setInput(label: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)
  if (!input) throw new Error(`找不到输入项：${label}`)
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function setSelect(label: string, value: string) {
  const select = host.querySelector<HTMLSelectElement>(`[aria-label="${label}"]`)
  if (!select) throw new Error(`找不到选择项：${label}`)
  await act(async () => { select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })) })
}

it('submits a 1x next-open research sample and displays execution evidence and the equity chart', async () => {
  await render()
  expect(cryptoApi.strategies).not.toHaveBeenCalled()
  expect(host.textContent).toContain('固定 1 倍研究')
  expect(host.textContent).toContain('下一根 K 线开盘价模拟成交')
  expect(host.textContent).toContain('手续费后、资金费前')
  expect(host.textContent).toContain('按前向轮询取得报价成交')
  await setSelect('研究策略', 'supertrend_pullback'); await settle()
  await setInput('样本K线数', '750')
  await setSelect('研究交易对', 'SOLUSDT')
  await setSelect('研究周期', '4h')
  await setInput('Supertrend ATR 周期', '12')
  await setInput('Supertrend ATR 倍数', '4')
  await setInput('趋势 EMA 周期', '34')
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(cryptoApi.researchBacktest).toHaveBeenCalledWith({
    strategy_id: 'supertrend_pullback', strategy_params: { atr_period: 12, atr_multiplier: 4, ema_period: 34 },
    symbol: 'SOLUSDT', interval: '4h', bars: 750, initial_cash: 10000, allocation_pct: 10, stop_loss_pct: 2, take_profit_pct: 4,
  })
  await settle()
  expect(host.textContent).toContain('回测已保存为研究记录；没有创建、启用或修改策略账户。')
  expect(host.textContent).toContain('资金费未计入')
  expect(host.textContent).toContain('模拟成交时间')
  expect(host.querySelector('[aria-label="研究回测权益曲线"]')).not.toBeNull()
  expect(cryptoApi.strategyAccounts).not.toHaveBeenCalled()
})

it('rejects sample sizes outside the documented 250–1000 bar range before posting', async () => {
  await render()
  await setInput('样本K线数', '249')
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(host.textContent).toContain('样本 K 线数须为 250 至 1000 的整数')
  expect(cryptoApi.researchBacktest).not.toHaveBeenCalled()
})

it('loads decisions only for the selected account and hides the prior account records after switching', async () => {
  await render()
  await clickText('决策记录')
  await settle()
  expect(cryptoApi.strategyAccounts).toHaveBeenCalledTimes(1)
  expect(host.querySelector('[aria-label="决策账户"]')?.textContent).not.toContain('legacy')
  await setSelect('决策账户', 'account-2')
  expect(cryptoApi.researchAccountDecisions).not.toHaveBeenCalled()
  await clickText('加载决策记录')
  await settle()
  expect(cryptoApi.researchAccountDecisions).toHaveBeenCalledWith('account-2')
  expect(host.textContent).toContain('风险限制 account-2')
  expect(host.textContent).toContain('权益 —')
  await setSelect('决策账户', 'account-1')
  expect(host.textContent).not.toContain('风险限制 account-2')
})

it('keeps deterministic review separate and makes AI interpretation an explicit optional request', async () => {
  await render()
  await clickText('每日复盘')
  await settle()
  expect(host.textContent).toContain('今日复盘按去重决策和账本数据统计')
  expect(host.textContent).toContain('BTCUSDT · 样本条件满足')
  expect(cryptoApi.researchReviewAI).not.toHaveBeenCalled()
  expect(host.textContent).toContain('消耗额度')
  await clickText('生成今日固定复盘')
  expect(cryptoApi.createResearchReview).toHaveBeenCalledTimes(1)
  expect(cryptoApi.researchReviewAI).not.toHaveBeenCalled()
  await settle()
  await clickText('生成并 AI 解读今日复盘')
  await settle()
  expect(cryptoApi.researchReviewAI).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('按需生成的复盘解读')
  expect(host.textContent).toContain('configured-model')
})

it('reports history errors without discarding the last available result', async () => {
  vi.mocked(cryptoApi.researchHistory).mockResolvedValueOnce({ runs: [run()] })
  await render()
  expect(host.textContent).toContain('BTCUSDT · 1h · 500 根')
  vi.mocked(cryptoApi.researchHistory).mockRejectedValueOnce(new Error('history offline'))
  await clickText('刷新')
  await settle()
  expect(host.textContent).toContain('history offline')
  expect(host.textContent).toContain('BTCUSDT · 1h · 500 根')
})
