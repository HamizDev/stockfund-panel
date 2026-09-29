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
}

export interface Candidate {
  symbol: string
  name: string
  strategies: { id: string; name: string; description: string }[]
  hit_count: number
  metrics: CandidateMetrics
  price_source: 'live' | 'daily'
}

export interface CandidateResponse {
  as_of: string | null
  updated_at: number | null
  coverage: { computed: number; total: number }
  quote: { live: boolean; provider: string | null; last_fetch_ms: number | null; age_ms: number | null }
  total: number
  items: Candidate[]
}

export async function getCandidates(): Promise<CandidateResponse> {
  const response = await fetch('/api/custom/ai-screener/candidates?limit=100', { credentials: 'same-origin' })
  if (!response.ok) throw new Error(`候选数据请求失败 (${response.status})`)
  return response.json()
}

export function formatPct(value: number | null): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`
}

export function formatMoney(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return value >= 1e8 ? `${(value / 1e8).toFixed(2)} 亿` : `${(value / 1e4).toFixed(0)} 万`
}
