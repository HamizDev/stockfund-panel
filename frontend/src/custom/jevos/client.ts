import { api } from '@/lib/api'
import type { JevosReview, JevosReviewLabel, JevosServiceStatus, JevosStatus } from './types'

const STATUSES = new Set<JevosServiceStatus>(['starting', 'ready', 'offline', 'not_ready', 'invalid_response'])
const LABELS = new Set<JevosReviewLabel>(['positive_clue', 'risk_clue', 'neutral', 'mixed', 'unclear', 'support', 'wait', 'conflict', 'insufficient'])
const SIGNALS = new Set(['long', 'short', 'flat', 'hold'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNullableTime(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isSafeInteger(value) && value > 0)
}

function isReview(value: unknown, expectedKind?: JevosReview['kind']): value is JevosReview {
  if (!isRecord(value)) return false
  return (value.kind === 'news' || value.kind === 'strategy')
    && (!expectedKind || value.kind === expectedKind)
    && typeof value.id === 'string' && value.id.length > 0 && value.id.length <= 64
    && typeof value.input_key === 'string' && /^[a-f0-9]{64}$/.test(value.input_key)
    && typeof value.model === 'string' && /^jevos[-a-zA-Z0-9_.]{0,63}$/.test(value.model)
    && typeof value.fingerprint === 'string' && /^[a-f0-9]{64}$/.test(value.fingerprint)
    && typeof value.generated_at_ms === 'number' && Number.isSafeInteger(value.generated_at_ms) && value.generated_at_ms > 0
    && isNullableTime(value.source_at_ms)
    && typeof value.label === 'string' && LABELS.has(value.label as JevosReviewLabel)
    && (value.original_signal === null || (typeof value.original_signal === 'string' && SIGNALS.has(value.original_signal)))
    && isRecord(value.probabilities) && Object.keys(value.probabilities).length > 0 && Object.keys(value.probabilities).length <= 8
    && Object.values(value.probabilities).every(probability => typeof probability === 'number' && Number.isFinite(probability) && probability >= 0 && probability <= 1)
    && Array.isArray(value.related_names) && value.related_names.length <= 8
    && value.related_names.every(name => typeof name === 'string' && name.length <= 40)
    && (value.method === undefined || value.method === 'noul-v1' || value.method === 'choice-v2')
    && (value.confidence == null || (typeof value.confidence === 'number' && Number.isFinite(value.confidence) && value.confidence >= 0 && value.confidence <= 1))
    && (value.tentative === undefined || typeof value.tentative === 'boolean')
    && (value.reason === undefined || (typeof value.reason === 'string' && value.reason.length <= 320))
    && (value.evidence === undefined || (Array.isArray(value.evidence) && value.evidence.length <= 1
      && value.evidence.every(quote => typeof quote === 'string' && quote.length > 0 && quote.length <= 280)))
}

function isReviewMap(value: unknown, kind: JevosReview['kind'], limit: number): value is Record<string, JevosReview> {
  return isRecord(value) && Object.keys(value).length <= limit
    && Object.entries(value).every(([key, review]) => key.length > 0 && key.length <= 64 && isReview(review, kind))
}

export function isJevosStatus(value: unknown): value is JevosStatus {
  return isRecord(value)
    && value.mode === 'shadow'
    && typeof value.status === 'string' && STATUSES.has(value.status as JevosServiceStatus)
    && (value.model === null || (typeof value.model === 'string' && value.model.length <= 64))
    && (value.fingerprint === null || (typeof value.fingerprint === 'string' && /^[a-f0-9]{64}$/.test(value.fingerprint)))
    && isNullableTime(value.last_check_ms)
    && typeof value.queued === 'number' && Number.isSafeInteger(value.queued) && value.queued >= 0
    && typeof value.completed === 'number' && Number.isSafeInteger(value.completed) && value.completed >= 0
    && isReviewMap(value.news, 'news', 400)
    && isReviewMap(value.strategies, 'strategy', 64)
    && Array.isArray(value.strategy_history) && value.strategy_history.length <= 30
    && value.strategy_history.every(review => isReview(review, 'strategy'))
}

export const jevosApi = {
  status: async (signal?: AbortSignal): Promise<JevosStatus> => {
    const result: unknown = await api.jevosStatus(signal)
    if (!isJevosStatus(result)) throw new Error('本地辅助判断状态响应无效')
    return result
  },
}
