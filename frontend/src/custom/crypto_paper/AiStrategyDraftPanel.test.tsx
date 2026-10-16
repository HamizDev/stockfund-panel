// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AiStrategyDraftPanel } from './AiStrategyDraftPanel'
import type { CryptoStrategyDraft } from './client'

vi.mock('@tanstack/react-query', () => ({ useQuery: () => ({ data: { configured: true, provider: 'test' }, isLoading: false, isError: false }) }))
vi.mock('react-router-dom', () => ({ Link: ({ children, to, ...props }: { children: unknown; to: string }) => <a href={to} {...props}>{children as never}</a> }))
vi.mock('@/lib/api', () => ({ api: { strategyAiStatus: vi.fn().mockResolvedValue({ configured: true, provider: 'test' }) } }))
vi.mock('@/components/financials/MarkdownRenderer', () => ({ MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div> }))
vi.mock('./client', () => ({ cryptoApi: { strategyDraft: vi.fn() } }))

const STORAGE_KEY = 'stockfund.crypto-paper.ai-strategy-draft.v1'
const draft: CryptoStrategyDraft = {
  name: 'Supertrend AI 草案', exchange: 'bitget', market: 'usdm', symbol: 'SOLUSDT', interval: '4h',
  strategy_id: 'supertrend_pullback', strategy_params: { atr_period: 14, atr_multiplier: 4, ema_period: 34 },
  leverage: 5, initial_cash: 2500, allocation_pct: 12, stop_loss_pct: 2.5, take_profit_pct: 5,
}
const request = { exchange: 'bitget' as const, market: 'usdm' as const, symbol: 'SOLUSDT' as const,
  interval: '4h' as const, leverage: 5, allocation_pct: 12 }
const result = {
  draft,
  rationale: '趋势研究草案',
  risk_notes: ['仅作研究'],
  evidence: { exchange: 'bitget', market: 'usdm', symbol: 'SOLUSDT', interval: '4h',
    retrieved_at_ms: 1800000000000, quote_asof_ms: 1800000000000, data_start_ms: 1790000000000,
    data_end_ms: 1800000000000, bars_count: 250, return_pct: 2, realized_volatility_pct: 1,
    atr_pct: 1.5, max_close_drawdown_pct: 4, taker_fee_rate: '0.0006', spread_bps: 2, funding_rate: null },
  diagnostics: { latest_signal: 'hold', signal_counts: { hold: 200 }, evaluated_bars: 250 },
  model: 'test-model', provider: 'test-provider', created_at_ms: 1800000000000,
}

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  window.sessionStorage.clear()
  window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ request, result }))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  window.sessionStorage.clear(); host.remove()
})

it('restores and names a Supertrend AI draft, then applies it to the parent form', async () => {
  const onApply = vi.fn<(value: CryptoStrategyDraft) => void>()
  await act(async () => root.render(
    <AiStrategyDraftPanel onApply={onApply} />,
  ))
  expect(host.textContent).toContain('Supertrend 回踩')
  expect(host.textContent).toContain('Supertrend ATR 14 · 4 倍 · EMA 34')
  const applyButton = [...host.querySelectorAll('button')].find(button => button.textContent === '填入账户创建表')!
  await act(async () => applyButton.click())
  expect(onApply).toHaveBeenCalledWith(draft)
})
