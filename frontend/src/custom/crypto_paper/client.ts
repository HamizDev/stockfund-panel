export type CryptoMarket = 'spot' | 'usdm'
export type CryptoExchange = 'binance' | 'bitget'
export type CryptoSymbol = 'BTCUSDT' | 'ETHUSDT' | 'SOLUSDT'
export type CryptoAction = 'buy' | 'sell' | 'open_long' | 'open_short' | 'close_long' | 'close_short'

export interface CryptoQuote {
  exchange?: CryptoExchange
  source?: string
  last?: string
  market: CryptoMarket
  symbol: CryptoSymbol
  bid: string
  ask: string
  mark: string | null
  last_funding_rate: string | null
  next_funding_time: number | null
  asof_ms: number
  step_size: string
  min_qty: string
  min_notional: string
}

export interface CryptoTrade {
  id: string
  at: string
  market: CryptoMarket
  symbol: CryptoSymbol
  action: CryptoAction | 'funding' | 'liquidation'
  quantity?: string
  price?: string
  fee?: string
  funding_amount?: string
  realized_pnl?: string
  leverage?: number | null
  kind?: 'trade' | 'funding' | 'liquidation'
}

export type CryptoStrategyId = 'ema_trend' | 'channel_breakout' | 'dmi_confirm' | 'squeeze_release' | 'supertrend_pullback' | 'bb_aroon'
export type CryptoStrategyInterval = '1h' | '4h'

export interface CryptoStrategyDefinition {
  id: CryptoStrategyId
  name: string
  description: string
  source_url?: string
  adaptation?: string
}

export interface CryptoStrategyModel {
  maintenance_margin_rate: string
  liquidation_fee_rate: string
  slippage_bps: number
  poll_seconds: number
}

export interface CryptoStrategyAccount {
  effective_readonly?: boolean
  readonly_reason?: string | null
  id: string
  name: string
  exchange: CryptoExchange
  taker_fee_rate?: string | null
  market: CryptoMarket
  symbol: CryptoSymbol
  strategy_id: CryptoStrategyId
  strategy_params?: Record<string, number>
  interval: CryptoStrategyInterval
  leverage: number
  initial_cash: string
  allocation_pct: number
  stop_loss_pct: number
  take_profit_pct: number
  enabled: boolean
  status: string
  last_error: string | null
  last_check_ms: number | null
  last_signal: string | null
  last_bar_time_ms: number | null
  cash: string
  equity: string | null
  total_pnl: string | null
  return_pct: number | null
  max_drawdown_pct: number | null
  fee_total: string
  funding_total: string
  liquidation_count: number
  trade_count: number
  positions: Record<string, unknown>
}

export interface CryptoStrategyAccountsResult {
  accounts: CryptoStrategyAccount[]
  runtime: { running: boolean; poll_seconds: number }
  model: CryptoStrategyModel
}

export interface CryptoStrategyNavPoint {
  at_ms: number
  equity: string
}

export type CryptoStrategyTrade = Partial<Omit<CryptoTrade, 'id'>> & Pick<CryptoTrade, 'id'> & {
  at?: string
  at_ms?: number
  action?: string
}

export interface CryptoStrategyAccountDetail {
  account: CryptoStrategyAccount
  trades: CryptoStrategyTrade[]
  nav: CryptoStrategyNavPoint[]
}

export type CryptoStrategyRunResult =
  | { account: CryptoStrategyAccount; busy?: false; stopped?: false; processed?: number }
  | { account?: CryptoStrategyAccount; busy: true; stopped?: false; processed?: number }
  | { account?: CryptoStrategyAccount; stopped: true; busy?: false; processed?: number }

export interface CryptoStrategyAccountCreate {
  name: string
  exchange?: CryptoExchange
  market: CryptoMarket
  symbol: CryptoSymbol
  strategy_id: CryptoStrategyId
  strategy_params?: Record<string, number>
  interval: CryptoStrategyInterval
  leverage: number
  initial_cash: number
  allocation_pct: number
  stop_loss_pct: number
  take_profit_pct: number
  request_id: string
}

export interface CryptoStrategyDraftRequest {
  exchange: CryptoExchange
  market: CryptoMarket
  symbol: CryptoSymbol
  interval: CryptoStrategyInterval
  leverage: number
  allocation_pct: number
  focus?: string
}

export type CryptoStrategyDraftParams =
  | { fast_period: number; slow_period: number }
  | { lookback: number }
  | { ma_period: number; adx_threshold: number }
  | { atr_period: number; atr_multiplier: number; ema_period: number }
  | { bb_period: number; aroon_period: number }

export interface CryptoStrategyPreviewRequest extends CryptoStrategyDraftRequest {
  strategy_id: CryptoStrategyId
  strategy_params: Record<string, number>
}

export interface CryptoStrategyPreviewResult {
  strategy_id: CryptoStrategyId
  strategy_params: Record<string, number>
  analysis: { target: string; reason: string; metrics: Record<string, number>; conditions: Record<string, boolean> }
  evidence: CryptoStrategyDraftEvidence
  source_url: string | null
  adaptation: string | null
  method: string
}

export interface CryptoStrategyDraft {
  name: string
  exchange: CryptoExchange
  market: CryptoMarket
  symbol: CryptoSymbol
  interval: CryptoStrategyInterval
  strategy_id: CryptoStrategyId
  strategy_params: CryptoStrategyDraftParams
  leverage: number
  initial_cash: number
  allocation_pct: number
  stop_loss_pct: number
  take_profit_pct: number
}

export interface CryptoStrategyDraftEvidence {
  exchange: CryptoExchange
  market: CryptoMarket
  symbol: CryptoSymbol
  interval: CryptoStrategyInterval
  retrieved_at_ms: number
  quote_asof_ms: number
  data_start_ms: number
  data_end_ms: number
  bars_count: number
  return_pct: number
  realized_volatility_pct: number
  atr_pct: number
  max_close_drawdown_pct: number
  taker_fee_rate: string
  spread_bps: number
  funding_rate: string | null
  stats_method?: string
}

export interface CryptoStrategyDraftResult {
  draft: CryptoStrategyDraft
  rationale: string
  risk_notes: string[]
  evidence: CryptoStrategyDraftEvidence
  diagnostics: {
    latest_signal: string
    signal_counts: Record<string, number>
    evaluated_bars: number
    method?: string
  }
  model: string
  provider: string
  created_at_ms: number
}

export interface CryptoStrategyAccountCompare extends Omit<CryptoStrategyAccountCreate, 'leverage'> {
  leverage_list: number[]
}

export interface CryptoResearchBacktestRequest {
  strategy_id: CryptoStrategyId
  strategy_params: Record<string, number> | null
  symbol: CryptoSymbol
  interval: CryptoStrategyInterval
  bars: number
  initial_cash: number
  allocation_pct: number
  stop_loss_pct: number
  take_profit_pct: number
}

export type CryptoResearchBacktestConfig = CryptoResearchBacktestRequest
export type CryptoResearchAmount = string | number

export interface CryptoResearchEvidence {
  bars_sha256?: string
  bars_count: number
  data_start_ms: number
  data_end_ms: number
  exchange: 'bitget'
  fee_rate: CryptoResearchAmount
  slippage_bps: number
}

export interface CryptoResearchMetrics {
  return_pct: number
  max_drawdown_pct: number
  closed_trades: number
  win_rate_pct: number | null
  fees_paid: CryptoResearchAmount
  ending_equity: CryptoResearchAmount
}

export interface CryptoResearchNavPoint {
  at_ms: number
  equity: CryptoResearchAmount
}

export interface CryptoResearchTrade {
  reason?: string
  signal_time_ms: number
  fill_time_ms: number
  action: string
  price: CryptoResearchAmount
  quantity: CryptoResearchAmount
  fee: CryptoResearchAmount
  realized_pnl: CryptoResearchAmount
}

export interface CryptoResearchBacktestSummary {
  id: string
  created_at_ms: number
  config: CryptoResearchBacktestConfig
  evidence: CryptoResearchEvidence
  metrics: CryptoResearchMetrics
  warnings: string[]
  method: string
}

export interface CryptoResearchBacktestRun extends CryptoResearchBacktestSummary {
  nav: CryptoResearchNavPoint[]
  trades: CryptoResearchTrade[]
}

export interface CryptoResearchDecision {
  at_ms: number
  bar_time_ms: number | null
  target: string | null
  status: string
  reason: string
  metrics: Record<string, string | number | null>
  conditions: Record<string, boolean>
  equity: string | null
  trades_added: number
}

export interface CryptoResearchReviewAccount {
  id: string
  name: string
  checks: number
  actions: number
  blocked: number
  last_reason: string | null
  trade_count: number
  equity: string | null
}

export interface CryptoResearchReview {
  date: string
  created_at_ms: number
  accounts: CryptoResearchReviewAccount[]
  summary: string
  candidates: unknown[]
  method: string
  ai?: { content: string; model: string; created_at_ms: number } | null
}

export interface CryptoResearchReviewAIResult {
  review: CryptoResearchReview
  ai: { content: string; model: string; created_at_ms: number }
}

export interface CryptoAccount {
  spot: {
    cash: string
    realized_pnl: string
    positions: Record<string, { qty: string; cost: string }>
  }
  usdm: {
    cash: string
    realized_pnl: string
    positions: Record<string, { side: 'long' | 'short'; qty: string; entry: string; margin: string; leverage: number }>
  }
  trades: CryptoTrade[]
}

export interface CryptoValuation {
  spot: { equity: string; holdings_value: string; total_pnl: string }
  usdm: { equity: string; margin: string; unrealized_pnl: string; total_pnl: string }
}

export type CryptoChartInterval = '1m' | '5m' | '15m' | '1h' | '4h'
export interface CryptoCandle {
  open_time_ms: number
  close_time_ms: number
  open: string
  high: string
  low: string
  close: string
  volume: string
}
export interface CryptoStreamStatus {
  connected: boolean
  fresh: boolean
  asof_ms: number | null
  error: string | null
  source: string
}
export interface CryptoTicker {
  symbol: CryptoSymbol
  bid: string
  ask: string
  mark: string
  last: string
  asof_ms: number
}

export type DemoExchange = 'okx' | 'bitget'
export type DemoSite = 'global' | 'us' | 'eea'
export type DemoAccountMode = 'classic' | 'uta'
export type DemoAccountState = 'unconfigured' | 'connected' | 'saved'

export interface DemoStatus {
  exchange: DemoExchange
  site: DemoSite
  account_mode: DemoAccountMode
  configured: boolean
  saved: boolean
  masked_key: string
  state: DemoAccountState
}

export interface DemoAccountsResult {
  accounts: DemoStatus[]
  persistence_supported: boolean
  readonly: true
}

export interface DemoBalance {
  currency: string
  equity: string | null
  available: string | null
  unrealized_pnl: string | null
}

export interface DemoPosition {
  symbol: string
  side: string
  quantity: string
  quantity_unit: string
  entry_price: string | null
  mark_price: string | null
  unrealized_pnl: string | null
  leverage: string | null
  margin_mode: string
}

export interface DemoOrder {
  order_id: string
  symbol: string
  side: string
  order_type: string
  quantity: string
  quantity_unit: string
  price: string | null
  filled_quantity: string
  status: string
  created_at_ms: number | null
}

export interface DemoFill {
  fill_id: string
  order_id: string
  symbol: string
  side: string
  price: string | null
  quantity: string
  quantity_unit: string
  fee: string | null
  fee_currency: string
  ts_ms: number | null
}

export interface DemoSnapshot {
  exchange: DemoExchange
  site: DemoSite
  account_mode: DemoAccountMode
  environment: 'demo'
  readonly: true
  observed_at: string
  stale: boolean
  total_equity: string | null
  equity_ccy: string
  balances: DemoBalance[]
  positions: DemoPosition[]
  orders: DemoOrder[]
  fills: DemoFill[]
  error?: string
}

export interface DemoConnectRequest {
  api_key: string
  secret_key: string
  passphrase: string
  site: DemoSite
  account_mode: DemoAccountMode
  save: boolean
}

export interface DemoMarketQuote {
  exchange: DemoExchange
  symbol: CryptoSymbol
  bid: string
  ask: string
  mark: string | null
  asof_ms: number
}

export interface DemoCandlesResult {
  bars: CryptoCandle[]
  closed_only: true
  exchange: DemoExchange
  symbol: CryptoSymbol
  interval: CryptoChartInterval
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/custom/crypto-paper${path}`, {
    credentials: 'same-origin',
    ...init,
  })
  const result = await response.json().catch(() => null)
  if (!response.ok) throw new Error(result?.detail || `请求失败 (${response.status})`)
  return result as T
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export const cryptoApi = {
  marketQuote: (symbol: CryptoSymbol) =>
    request<{ quote: CryptoQuote; stream: CryptoStreamStatus }>(`/market/${symbol}/quote`),
  candles: (symbol: CryptoSymbol, interval: CryptoChartInterval) =>
    request<{ symbol: CryptoSymbol; interval: CryptoChartInterval; bars: CryptoCandle[]; closed_only: boolean }>(`/market/${symbol}/candles?interval=${interval}`),
  demoAccounts: async () => {
    const result = await request<DemoAccountsResult>('/demo/accounts')
    if (!Array.isArray(result?.accounts) || typeof result.persistence_supported !== 'boolean' || result.readonly !== true) {
      throw new Error('官方模拟账户状态响应格式无效')
    }
    return result
  },
  demoAccount: async (exchange: DemoExchange, signal?: AbortSignal) => {
    const result = await request<DemoSnapshot>(`/demo/accounts/${exchange}?refresh=true`, { signal })
    if (result?.exchange !== exchange || result?.environment !== 'demo' || result?.readonly !== true) {
      throw new Error('官方模拟账户快照响应格式无效')
    }
    return result
  },
  demoConnect: async (exchange: DemoExchange, body: DemoConnectRequest) => {
    const result = await request<{ account: DemoStatus; snapshot: DemoSnapshot }>(`/demo/accounts/${exchange}/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (result?.account?.exchange !== exchange || result.account.configured !== true
      || result.snapshot?.exchange !== exchange || result.snapshot.environment !== 'demo' || result.snapshot.readonly !== true) {
      throw new Error('官方模拟账户连接响应格式无效')
    }
    return result
  },
  demoDisconnect: async (exchange: DemoExchange) => {
    const result = await request<{ account: DemoStatus }>(`/demo/accounts/${exchange}/disconnect`, { method: 'POST' })
    if (result?.account?.exchange !== exchange || result.account.configured !== false || result.account.state !== 'unconfigured') {
      throw new Error('官方模拟账户断开响应格式无效')
    }
    return result
  },
  demoCandles: (exchange: DemoExchange, site: DemoSite, symbol: CryptoSymbol, interval: CryptoChartInterval, signal?: AbortSignal) => {
    const query = new URLSearchParams({ interval, site })
    return request<DemoCandlesResult>(`/demo/market/${exchange}/${symbol}/candles?${query}`, { signal }).then(result => {
      if (result?.exchange !== exchange || result.symbol !== symbol || result.interval !== interval
        || result.closed_only !== true || !Array.isArray(result.bars)) {
        throw new Error('官方公开 K 线响应格式无效')
      }
      return result
    })
  },
  demoMarketQuote: (exchange: DemoExchange, site: DemoSite, symbol: CryptoSymbol, signal?: AbortSignal) => {
    const query = new URLSearchParams({ site })
    return request<DemoMarketQuote>(`/demo/market/${exchange}/${symbol}/quote?${query}`, { signal }).then(result => {
      if (result?.exchange !== exchange || result.symbol !== symbol || typeof result.bid !== 'string'
        || typeof result.ask !== 'string' || !Number.isFinite(result.asof_ms)) {
        throw new Error('官方公开报价响应格式无效')
      }
      return result
    })
  },
  marketStream: (symbol: CryptoSymbol) => new EventSource(`/api/custom/crypto-paper/market/${symbol}/stream`),
  account: async () => {
    const result = await request<CryptoAccount>('/account')
    if (!result?.spot?.positions || !result?.usdm?.positions || !Array.isArray(result.trades)) {
      throw new Error('虚拟币模拟账本响应格式无效')
    }
    return result
  },
  valuation: async () => {
    const result = await request<CryptoValuation>('/valuation')
    if (!result?.spot?.equity || !result?.usdm?.equity) throw new Error('虚拟币估值响应格式无效')
    return result
  },
  quote: async (market: CryptoMarket, symbol: CryptoSymbol) => {
    const result = await request<CryptoQuote>(`/quote/${market}/${symbol}`)
    if (result?.symbol !== symbol || result?.market !== market || !result?.bid || !result?.ask) {
      throw new Error('币安行情响应格式无效')
    }
    return result
  },
  order: (body: { market: CryptoMarket; symbol: CryptoSymbol; action: CryptoAction; quantity: string; leverage: number; request_id: string }) =>
    request<{ order: CryptoTrade; account: CryptoAccount }>('/orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  strategies: async () => {
    const result = await request<{ strategies: CryptoStrategyDefinition[]; model: CryptoStrategyModel }>('/strategies')
    if (!Array.isArray(result?.strategies)) throw new Error('策略目录响应格式无效')
    return result
  },
  strategyDraft: (body: CryptoStrategyDraftRequest, signal?: AbortSignal) =>
    request<CryptoStrategyDraftResult>('/strategy-draft', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    }),
  strategyPreview: (body: CryptoStrategyPreviewRequest, signal?: AbortSignal) =>
    request<CryptoStrategyPreviewResult>('/strategy-preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
    }),
  strategyAccounts: async () => {
    const result = await request<CryptoStrategyAccountsResult>('/strategy-accounts')
    if (!Array.isArray(result?.accounts) || !result?.runtime || !result?.model) {
      throw new Error('策略模拟账户响应格式无效')
    }
    return result
  },
  researchBacktest: async (body: CryptoResearchBacktestRequest): Promise<CryptoResearchBacktestRun> => {
    const result = await request<CryptoResearchBacktestRun>('/research/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!isRecord(result) || typeof result.id !== 'string' || !isRecord(result.config)
      || !isRecord(result.evidence) || !isRecord(result.metrics)
      || !Array.isArray(result.nav) || !Array.isArray(result.trades) || !Array.isArray(result.warnings)) {
      throw new Error('研究回测响应格式无效')
    }
    return result as CryptoResearchBacktestRun
  },
  researchHistory: async () => {
    const result = await request<{ runs: CryptoResearchBacktestSummary[] }>('/research/history')
    if (!Array.isArray(result?.runs)) throw new Error('研究回测历史响应格式无效')
    return result
  },
  researchRun: async (id: string) => {
    if (!id) throw new Error('研究回测记录 ID 不能为空')
    const result = await request<CryptoResearchBacktestRun>(`/research/runs/${encodeURIComponent(id)}`)
    if (result?.id !== id || !Array.isArray(result.nav) || !Array.isArray(result.trades)) {
      throw new Error('研究回测明细响应格式无效')
    }
    return result
  },
  researchAccountDecisions: async (id: string) => {
    if (!id) throw new Error('策略账户 ID 不能为空')
    const result = await request<{ decisions: CryptoResearchDecision[] }>(`/research/accounts/${encodeURIComponent(id)}/decisions`)
    if (!Array.isArray(result?.decisions)) throw new Error('策略决策记录响应格式无效')
    return result
  },
  researchReviews: async () => {
    const result = await request<{ reviews: CryptoResearchReview[] }>('/research/reviews')
    if (!Array.isArray(result?.reviews)) throw new Error('每日复盘响应格式无效')
    return result
  },
  createResearchReview: () => request<unknown>('/research/review', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  }),
  researchReviewAI: async (): Promise<CryptoResearchReviewAIResult> => {
    const result = await request<CryptoResearchReviewAIResult>('/research/review-ai', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    })
    if (!isRecord(result?.review) || !isRecord(result?.ai) || typeof result.ai.content !== 'string') {
      throw new Error('AI 复盘解读响应格式无效')
    }
    return result as CryptoResearchReviewAIResult
  },
  createStrategyAccount: async (body: CryptoStrategyAccountCreate) => {
    const result = await request<{ account: CryptoStrategyAccount }>('/strategy-accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!result?.account?.id) throw new Error('策略模拟账户创建响应格式无效')
    return result
  },
  compareStrategyAccounts: async (body: CryptoStrategyAccountCompare) => {
    const result = await request<{ accounts: CryptoStrategyAccount[] }>('/strategy-accounts/compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!Array.isArray(result?.accounts)) throw new Error('杠杆对照组创建响应格式无效')
    return result
  },
  setStrategyAccountEnabled: async (id: string, enabled: boolean) => {
    const result = await request<{ account: CryptoStrategyAccount }>(`/strategy-accounts/${encodeURIComponent(id)}/enabled`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    if (!result?.account?.id) throw new Error('策略启停响应格式无效')
    return result
  },
  runStrategyAccount: async (id: string) => {
    const result = await request<CryptoStrategyRunResult>(`/strategy-accounts/${encodeURIComponent(id)}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
    if (!result || typeof result !== 'object') throw new Error('策略账户检查响应格式无效')
    if (result.busy || result.stopped) return result
    if (!result.account?.id) throw new Error('策略账户检查响应格式无效')
    return result
  },
  strategyAccountDetail: async (id: string) => {
    const result = await request<CryptoStrategyAccountDetail>(`/strategy-accounts/${encodeURIComponent(id)}`)
    if (!result?.account?.id || !Array.isArray(result.trades) || !Array.isArray(result.nav)) {
      throw new Error('策略账户明细响应格式无效')
    }
    return result
  },
}
