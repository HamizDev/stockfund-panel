import { afterEach, expect, it, vi } from 'vitest'
import { cryptoApi, type CryptoResearchBacktestRequest, type CryptoResearchBacktestRun, type CryptoResearchReview } from './client'

function response(data: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => data } as Response
}

afterEach(() => vi.unstubAllGlobals())

const config: CryptoResearchBacktestRequest = {
  strategy_id: 'supertrend_pullback', strategy_params: { atr_period: 10, atr_multiplier: 3, ema_period: 20 },
  symbol: 'SOLUSDT', interval: '4h', bars: 500, initial_cash: 10000,
  allocation_pct: 10, stop_loss_pct: 2, take_profit_pct: 4,
}
const run: CryptoResearchBacktestRun = {
  id: 'run-1', created_at_ms: 1800000000000, config,
  evidence: { bars_count: 500, data_start_ms: 1790000000000, data_end_ms: 1800000000000,
    exchange: 'bitget', fee_rate: '0.0006', slippage_bps: 5 },
  metrics: { return_pct: 4.2, max_drawdown_pct: 3.1, closed_trades: 8, win_rate_pct: 62.5, fees_paid: '12.4', ending_equity: '10420' },
  nav: [{ at_ms: 1800000000000, equity: '10420' }],
  trades: [{ signal_time_ms: 1799990000000, fill_time_ms: 1800000000000, action: 'open_long', price: '100', quantity: '1', fee: '0.06', realized_pnl: '0' }],
  warnings: [], method: '收盘信号，下一根开盘成交',
}
const review: CryptoResearchReview = {
  date: '2026-10-16', created_at_ms: 1800000000000, accounts: [], summary: '今日固定统计', candidates: [], method: '去重决策与账本统计',
}

it('uses the shared request wrapper for research backtests, history, details and decisions', async () => {
  const fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValueOnce(response({ ...run, id: 'run/one' }))
  await cryptoApi.researchBacktest(config)
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/research/backtest', expect.objectContaining({
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config),
  }))

  fetchMock.mockResolvedValueOnce(response({ runs: [run] }))
  await cryptoApi.researchHistory()
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/research/history', expect.objectContaining({ credentials: 'same-origin' }))

  fetchMock.mockResolvedValueOnce(response({ ...run, id: 'run/one' }))
  await cryptoApi.researchRun('run/one')
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/research/runs/run%2Fone', expect.any(Object))

  fetchMock.mockResolvedValueOnce(response({ decisions: [] }))
  await cryptoApi.researchAccountDecisions('account/one')
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/research/accounts/account%2Fone/decisions', expect.any(Object))
})

it('uses deterministic daily review and optional AI review routes', async () => {
  const fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValueOnce(response({ reviews: [review] }))
  await cryptoApi.researchReviews()
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/research/reviews', expect.any(Object))

  fetchMock.mockResolvedValueOnce(response({ review }))
  await cryptoApi.createResearchReview()
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/research/review', expect.objectContaining({
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  }))

  const ai = { content: '按需解释', model: 'configured-model', created_at_ms: 1800000001000 }
  fetchMock.mockResolvedValueOnce(response({ review: { ...review, ai }, ai }))
  await cryptoApi.researchReviewAI()
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/research/review-ai', expect.objectContaining({
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  }))
})
