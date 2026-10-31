export type ResearchDirection = 'up' | 'down' | 'unknown' | (string & {})

export interface ResearchDecisionItem {
  id: string
  symbol: string
  name?: string
  created_at: string
  horizon_sessions: number
  direction: ResearchDirection
  reference_price: number | null
  status: string
  target_date: string | null
  /** Fractional raw-price change, e.g. 0.05 means +5%. */
  price_return: number | null
  hit: boolean | null
}

export interface ResearchDecisionStatistics {
  evaluated: number
  direction_hit_rate: number | null
  always_up_hit_rate: number | null
  always_down_hit_rate: number | null
}

export interface ResearchDecisionList {
  items: ResearchDecisionItem[]
  total: number
  recent_limit: number
  statistics: ResearchDecisionStatistics
  limits: string
}

export interface ResearchOutcome {
  id?: string
  status: string
  target_date?: string | null
  actual_price?: number | null
  /** Fractional raw-price change, not a trading return. */
  price_return?: number | null
  hit?: boolean | null
  message?: string | null
  checked_at?: string | null
  [key: string]: unknown
}

export interface ResearchDecisionRecord {
  content?: string
  asset_type?: 'stock' | 'etf' | 'index'
  id: string
  symbol: string
  name?: string
  created_at: string
  requested_at: string
  horizon_sessions: number
  direction: ResearchDirection
  reference_price: number | null
  snapshot: Record<string, unknown>
  portfolio: unknown
  review: unknown
  score_definition?: string
}

export interface ResearchDecisionDetail {
  record: ResearchDecisionRecord
  outcome: ResearchOutcome | null
}
