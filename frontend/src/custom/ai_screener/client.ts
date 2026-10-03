export interface CandidateMetrics {
  close: number | null
  change_pct: number | null
  amount: number | null
  volume: number | null
  turnover_rate: number | null
  pe_ttm: number | null
  pb: number | null
  ma5: number | null
  ma20: number | null
  ma60: number | null
  raw_close?: number | null
  vol_ratio_5d?: number | null
  macd_dif?: number | null
  macd_dea?: number | null
  macd_hist?: number | null
  kdj_k?: number | null
  kdj_d?: number | null
  kdj_j?: number | null
}

export interface Candidate {
  symbol: string
  name: string
  strategies: { id: string; name: string; description: string }[]
  hit_count: number
  metrics: CandidateMetrics
  price_source: 'live' | 'daily'
  price_basis?: 'raw' | 'qfq'
  technical_as_of?: string | null
}

export interface CandidateResponse {
  asset_type: 'stock' | 'etf'
  as_of: string | null
  updated_at: number | null
  cache_available: boolean
  error?: string
  coverage: { computed: number; total: number }
  quote: { live: boolean; provider: string | null; last_fetch_ms: number | null; age_ms: number | null }
  total: number
  items: Candidate[]
}

export async function getCandidates(assetType: 'stock' | 'etf' = 'stock'): Promise<CandidateResponse> {
  const query = new URLSearchParams({ asset_type: assetType, limit: '100' })
  const response = await fetch('/api/custom/ai-screener/candidates?' + query.toString(), { credentials: 'same-origin' })
  if (!response.ok) throw new Error('候选数据请求失败 (' + response.status + ')')
  return response.json()
}

export type CandidateAnalysisEvent =
  | { type: 'meta'; symbol?: string; as_of?: string | null; summary?: string }
  | { type: 'delta'; content?: string }
  | { type: 'error'; message?: string }
  | { type: 'done' }
  | { type: 'ping' }

const CANDIDATE_ANALYSIS_EVENT_TYPES = new Set(['meta', 'delta', 'error', 'done', 'ping'])

function parseCandidateAnalysisEvent(line: string): CandidateAnalysisEvent {
  let value: unknown
  try {
    value = JSON.parse(line)
  } catch {
    throw new Error('AI 分析流包含无效 JSON 行')
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('AI 分析流事件格式无效')
  }
  const type = (value as { type?: unknown }).type
  if (typeof type !== 'string' || !CANDIDATE_ANALYSIS_EVENT_TYPES.has(type)) {
    throw new Error('AI 分析流事件类型无效')
  }
  const fields = value as Record<string, unknown>
  for (const field of ['content', 'message', 'symbol', 'summary']) {
    if (fields[field] !== undefined && typeof fields[field] !== 'string') throw new Error('AI 分析流内容格式无效')
  }
  if (fields.as_of !== undefined && fields.as_of !== null && typeof fields.as_of !== 'string') throw new Error('AI 分析日期格式无效')
  return value as CandidateAnalysisEvent
}

export async function* analyzeCandidate(
  symbol: string,
  signal?: AbortSignal,
): AsyncGenerator<CandidateAnalysisEvent> {
  const response = await fetch('/api/stock-analysis/analyze', {
    method: 'POST',
    credentials: 'same-origin',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol, research_plan: true }),
  })
  if (!response.ok) {
    let message = String(response.status) + ' ' + response.statusText
    try {
      const body = await response.json() as { detail?: unknown; message?: unknown }
      if (typeof body.detail === 'string') message = body.detail
      else if (typeof body.message === 'string') message = body.message
    } catch {
      // Retain the HTTP status when the server response is not JSON.
    }
    throw new Error('AI 分析请求失败：' + message)
  }
  if (!response.body) throw new Error('AI 分析流响应缺少 body')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) {
        buffer += decoder.decode()
        break
      }
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        const trimmed = line.trim()
        if (trimmed) yield parseCandidateAnalysisEvent(trimmed)
      }
    }
    const trailing = buffer.trim()
    if (trailing) yield parseCandidateAnalysisEvent(trailing)
  } finally {
    try {
      await reader.cancel()
    } catch {
      // The stream may already be closed or aborted.
    }
    reader.releaseLock()
  }
}

export function formatPct(value: number | null): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`
}

export function formatMoney(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return value >= 1e8 ? `${(value / 1e8).toFixed(2)} 亿` : `${(value / 1e4).toFixed(0)} 万`
}
