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
  action: CryptoAction
  quantity: string
  price: string
  fee: string
  realized_pnl: string
  leverage: number | null
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
}
