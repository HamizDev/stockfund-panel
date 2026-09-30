export type CryptoMarket = 'spot' | 'usdm'
export type CryptoSymbol = 'BTCUSDT' | 'ETHUSDT' | 'SOLUSDT'
export type CryptoAction = 'buy' | 'sell' | 'open_long' | 'open_short' | 'close_long' | 'close_short'

export interface CryptoQuote {
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

export type CryptoStrategyId = 'ema_trend' | 'channel_breakout'
export type CryptoStrategyInterval = '1h' | '4h'

export interface CryptoStrategyDefinition {
  id: CryptoStrategyId
  name: string
  description: string
}

export interface CryptoStrategyModel {
  maintenance_margin_rate: string
  liquidation_fee_rate: string
  slippage_bps: number
  poll_seconds: number
}

export interface CryptoStrategyAccount {
  id: string
  name: string
  market: CryptoMarket
  symbol: CryptoSymbol
  strategy_id: CryptoStrategyId
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
  market: CryptoMarket
  symbol: CryptoSymbol
  strategy_id: CryptoStrategyId
  interval: CryptoStrategyInterval
  leverage: number
  initial_cash: number
  allocation_pct: number
  stop_loss_pct: number
  take_profit_pct: number
  request_id: string
}

export interface CryptoStrategyAccountCompare extends Omit<CryptoStrategyAccountCreate, 'leverage'> {
  leverage_list: number[]
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/custom/crypto-paper${path}`, {
    credentials: 'same-origin',
    ...init,
  })
  const result = await response.json().catch(() => null)
  if (!response.ok) throw new Error(result?.detail || `请求失败 (${response.status})`)
  return result as T
}

export const cryptoApi = {
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
  strategyAccounts: async () => {
    const result = await request<CryptoStrategyAccountsResult>('/strategy-accounts')
    if (!Array.isArray(result?.accounts) || !result?.runtime || !result?.model) {
      throw new Error('策略模拟账户响应格式无效')
    }
    return result
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
