import { request, type CryptoSymbol, type DemoExchange } from './client'

export type DemoTradingAction = 'open_long' | 'open_short' | 'close_long' | 'close_short'

export interface DemoTradingDecision {
  at_ms: number
  status: string
  reason: string
  target?: string | null
}

export interface DemoTradingOrder {
  client_id: string
  order_id: string | null
  state: 'pending' | 'submitted' | 'uncertain' | 'rejected' | 'confirmed'
  exchange_state?: string | null
  symbol: string
  action: string
  quantity: number | string
  estimated_notional: number | string
  created_at_ms: number
}

export interface DemoTradingStatus {
  armed: boolean
  auto: boolean
  source_account_id: string | null
  symbol: string
  max_notional: number
  daily_loss_limit: number
  reason: string
  managed: boolean
  decisions: DemoTradingDecision[]
  orders: DemoTradingOrder[]
}

export interface DemoTradingArmRequest {
  enabled: boolean
  confirm_demo: true
  symbol: CryptoSymbol
  source_account_id: string | null
  max_notional: number
  daily_loss_limit: number
}

export interface DemoTradingPlanRequest {
  symbol: CryptoSymbol
  action: DemoTradingAction
  notional: number
}

export interface DemoTradingPlan {
  id: string
  expires_at_ms: number
  symbol: string
  action: DemoTradingAction
  quantity: number | string
  quantity_unit: string
  estimated_notional: number | string
  price: number | string
  reduce_only: boolean
  leverage: 1
  margin_mode: 'isolated'
  warnings: string[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isTradingOrder(value: unknown): value is DemoTradingOrder {
  return isRecord(value)
    && typeof value.client_id === 'string'
    && (value.order_id === null || typeof value.order_id === 'string')
    && ['pending', 'submitted', 'uncertain', 'rejected', 'confirmed'].includes(String(value.state))
    && typeof value.symbol === 'string'
    && typeof value.action === 'string'
    && (typeof value.quantity === 'number' || typeof value.quantity === 'string')
    && (typeof value.estimated_notional === 'number' || typeof value.estimated_notional === 'string')
    && typeof value.created_at_ms === 'number'
}

function isTradingStatus(value: unknown): value is DemoTradingStatus {
  return isRecord(value)
    && typeof value.armed === 'boolean'
    && typeof value.auto === 'boolean'
    && (value.source_account_id === null || typeof value.source_account_id === 'string')
    && typeof value.symbol === 'string'
    && typeof value.max_notional === 'number'
    && typeof value.daily_loss_limit === 'number'
    && typeof value.reason === 'string'
    && typeof value.managed === 'boolean'
    && Array.isArray(value.decisions)
    && Array.isArray(value.orders)
    && value.orders.every(isTradingOrder)
}

function isPlan(value: unknown): value is DemoTradingPlan {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.expires_at_ms === 'number'
    && typeof value.symbol === 'string'
    && typeof value.action === 'string'
    && (typeof value.quantity === 'number' || typeof value.quantity === 'string')
    && typeof value.quantity_unit === 'string'
    && (typeof value.estimated_notional === 'number' || typeof value.estimated_notional === 'string')
    && (typeof value.price === 'number' || typeof value.price === 'string')
    && typeof value.reduce_only === 'boolean'
    && value.leverage === 1
    && value.margin_mode === 'isolated'
    && Array.isArray(value.warnings)
}

function tradingPath(exchange: DemoExchange) {
  return `/demo/trading/${encodeURIComponent(exchange)}`
}

export const demoExecutionApi = {
  status: async (exchange: DemoExchange, signal?: AbortSignal) => {
    const result = await request<DemoTradingStatus>(tradingPath(exchange), { signal })
    if (!isTradingStatus(result)) throw new Error('官方模拟执行状态响应格式无效')
    return result
  },
  arm: async (exchange: DemoExchange, body: DemoTradingArmRequest) => {
    const result = await request<DemoTradingStatus>(`${tradingPath(exchange)}/arm`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!isTradingStatus(result)) throw new Error('官方模拟执行启停响应格式无效')
    return result
  },
  plan: async (exchange: DemoExchange, body: DemoTradingPlanRequest) => {
    const result = await request<DemoTradingPlan>(`${tradingPath(exchange)}/plan`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!isPlan(result)) throw new Error('模拟交易计划响应格式无效')
    return result
  },
  submit: async (exchange: DemoExchange, planId: string) => {
    const result = await request<{ order: DemoTradingOrder }>(`${tradingPath(exchange)}/submit`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan_id: planId, confirm: true }),
    })
    if (!isRecord(result) || !isTradingOrder(result.order)) throw new Error('模拟订单提交响应格式无效')
    return result
  },
  reconcile: async (exchange: DemoExchange, clientId: string) => {
    if (!clientId) throw new Error('客户端订单编号不能为空')
    const result = await request<{ order: DemoTradingOrder }>(`${tradingPath(exchange)}/reconcile/${encodeURIComponent(clientId)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    })
    if (!isRecord(result) || !isTradingOrder(result.order)) throw new Error('模拟订单对账响应格式无效')
    return result
  },
}
