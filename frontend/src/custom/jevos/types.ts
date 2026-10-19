export type JevosServiceStatus = 'starting' | 'ready' | 'offline' | 'not_ready' | 'invalid_response'

export type JevosReviewLabel =
  | 'positive_clue' | 'risk_clue' | 'neutral' | 'mixed' | 'unclear'
  | 'support' | 'wait' | 'conflict' | 'insufficient'

export interface JevosReview {
  kind: 'news' | 'strategy'
  id: string
  input_key: string
  model: string
  fingerprint: string
  generated_at_ms: number
  source_at_ms: number | null
  label: JevosReviewLabel
  original_signal: string | null
  probabilities: Record<string, number>
  related_names: string[]
  method?: 'noul-v1' | 'choice-v2'
  confidence?: number | null
  tentative?: boolean
  reason?: string
  evidence?: string[]
}

export interface JevosStatus {
  mode: 'shadow'
  status: JevosServiceStatus
  model: string | null
  fingerprint: string | null
  last_check_ms: number | null
  queued: number
  completed: number
  news: Record<string, JevosReview>
  strategies: Record<string, JevosReview>
  strategy_history: JevosReview[]
}
