import { request } from './client'

export type DemoBotsExchange = 'okx'

export interface DemoBotPosition {
  side: string
  quantity: string
  entry_price: string
  notional: string
}

export interface DemoBotMetrics {
  closed_trades: number
  wins: number
  win_rate_pct: number | null
  realized_pnl_usdt: string
  fees_usdt: string
  open_pnl_usdt: string | null
  allocation_equity_usdt: string | null
  max_drawdown_pct: number
  funding_included: false
}

export interface DemoBotOrder {
  client_id: string
  action: string
  state: string
  created_at_ms: number
  filled_quantity?: string
  avg_price?: string
  fee_usdt?: string
}

export interface DemoBot {
  id: string
  name: string
  symbol: string
  strategy_id: string
  description?: string
  interval: string
  budget_usdt: number
  max_notional: number
  leverage?: number
  enabled: boolean
  armed: boolean
  status: string
  reason: string
  last_check_ms: number | null
  last_bar_ms: number | null
  last_signal: string | null
  position: DemoBotPosition | null
  position_verified?: boolean
  metrics: DemoBotMetrics
  orders: DemoBotOrder[]
}

export interface DemoBotsStatus {
  exchange: 'okx'
  environment: 'demo'
  connection_ready: boolean
  legacy_armed: boolean
  bots: DemoBot[]
}

export interface DemoBotToggleRequest {
  enabled: boolean
  confirm_demo: boolean
}

export interface DemoSetupCheck {
  label: string
  passed: boolean
  actual: string
  required: string
}

export interface DemoBotsPreflight {
  exchange: 'okx'
  environment: 'demo'
  checked_at_ms: number
  ready: boolean
  checks: DemoSetupCheck[]
  bots: { id: string; name: string; symbol: string; checks: DemoSetupCheck[] }[]
}

function isCheck(value: unknown): value is DemoSetupCheck {
  return isRecord(value) && typeof value.label === 'string' && typeof value.passed === 'boolean'
    && typeof value.actual === 'string' && typeof value.required === 'string'
}

function isPreflight(value: unknown): value is DemoBotsPreflight {
  return isRecord(value) && value.exchange === 'okx' && value.environment === 'demo'
    && typeof value.checked_at_ms === 'number' && Number.isFinite(value.checked_at_ms)
    && typeof value.ready === 'boolean' && Array.isArray(value.checks) && value.checks.every(isCheck)
    && Array.isArray(value.bots) && value.bots.every(bot => isRecord(bot)
      && typeof bot.id === 'string' && typeof bot.name === 'string' && typeof bot.symbol === 'string'
      && Array.isArray(bot.checks) && bot.checks.every(isCheck))
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isFinite(value))
}

function isPosition(value: unknown): value is DemoBotPosition | null {
  return value === null || (isRecord(value)
    && typeof value.side === 'string'
    && typeof value.quantity === 'string'
    && typeof value.entry_price === 'string'
    && typeof value.notional === 'string')
}

function isMetrics(value: unknown): value is DemoBotMetrics {
  return isRecord(value)
    && typeof value.closed_trades === 'number' && Number.isFinite(value.closed_trades)
    && typeof value.wins === 'number' && Number.isFinite(value.wins)
    && isNullableNumber(value.win_rate_pct)
    && typeof value.realized_pnl_usdt === 'string'
    && typeof value.fees_usdt === 'string'
    && isNullableString(value.open_pnl_usdt)
    && isNullableString(value.allocation_equity_usdt)
    && typeof value.max_drawdown_pct === 'number' && Number.isFinite(value.max_drawdown_pct)
    && value.funding_included === false
}

function isOrder(value: unknown): value is DemoBotOrder {
  return isRecord(value)
    && typeof value.client_id === 'string'
    && typeof value.action === 'string'
    && typeof value.state === 'string'
    && typeof value.created_at_ms === 'number' && Number.isFinite(value.created_at_ms)
    && (value.filled_quantity === undefined || typeof value.filled_quantity === 'string')
    && (value.avg_price === undefined || typeof value.avg_price === 'string')
    && (value.fee_usdt === undefined || typeof value.fee_usdt === 'string')
}

function isBot(value: unknown): value is DemoBot {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.name === 'string'
    && typeof value.symbol === 'string'
    && typeof value.strategy_id === 'string'
    && (value.description === undefined || typeof value.description === 'string')
    && typeof value.interval === 'string'
    && typeof value.budget_usdt === 'number' && Number.isFinite(value.budget_usdt)
    && typeof value.max_notional === 'number' && Number.isFinite(value.max_notional)
    && (value.leverage === undefined || value.leverage === 10)
    && typeof value.enabled === 'boolean'
    && typeof value.armed === 'boolean'
    && typeof value.status === 'string'
    && typeof value.reason === 'string'
    && isNullableNumber(value.last_check_ms)
    && isNullableNumber(value.last_bar_ms)
    && isNullableString(value.last_signal)
    && isPosition(value.position)
    && (value.position_verified === undefined || typeof value.position_verified === 'boolean')
    && isMetrics(value.metrics)
    && Array.isArray(value.orders) && value.orders.every(isOrder)
}

function isDemoBotsStatus(value: unknown): value is DemoBotsStatus {
  return isRecord(value)
    && value.exchange === 'okx'
    && value.environment === 'demo'
    && typeof value.connection_ready === 'boolean'
    && typeof value.legacy_armed === 'boolean'
    && Array.isArray(value.bots) && value.bots.every(isBot)
}

function botsPath(exchange: DemoBotsExchange) {
  return `/demo/bots/${encodeURIComponent(exchange)}`
}

export const demoBotsApi = {
  preflight: async (exchange: DemoBotsExchange): Promise<DemoBotsPreflight> => {
    const result = await request<unknown>(`${botsPath(exchange)}/preflight`)
    if (!isPreflight(result)) throw new Error('官方模拟检查响应格式无效')
    return result
  },
  status: async (exchange: DemoBotsExchange, signal?: AbortSignal): Promise<DemoBotsStatus> => {
    const result = await request<unknown>(botsPath(exchange), { signal })
    if (!isDemoBotsStatus(result)) throw new Error('官方模拟策略状态响应格式无效')
    return result
  },
  createPresets: (exchange: DemoBotsExchange) => request<unknown>(`${botsPath(exchange)}/presets`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  }),
  setEnabled: (exchange: DemoBotsExchange, body: DemoBotToggleRequest) => request<unknown>(`${botsPath(exchange)}/enabled`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  setBotEnabled: (exchange: DemoBotsExchange, id: string, body: DemoBotToggleRequest) =>
    request<unknown>(`${botsPath(exchange)}/${encodeURIComponent(id)}/enabled`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
  reconcile: (exchange: DemoBotsExchange, id: string) => request<unknown>(`${botsPath(exchange)}/${encodeURIComponent(id)}/reconcile`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  }),
}
