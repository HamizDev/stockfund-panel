import type { KronosEvaluation, KronosEvaluationState } from './evaluationTypes'
import type { KronosComparison, KronosComparisonSummary } from './comparisonTypes'
import { isKronosComparisonActive } from './comparisonTypes'
import type { KronosRollingStatus } from './types'

const PENDING_EVALUATION_STATES = new Set<KronosEvaluationState>(['pending', 'partial', 'unavailable'])

export function kronosEvaluationRefetchInterval(evaluation: KronosEvaluation | undefined, failed = false): number | false {
  if (evaluation?.state === 'complete' || evaluation?.state === 'not_applicable') return false
  if (failed) return 60_000
  return evaluation && PENDING_EVALUATION_STATES.has(evaluation.state) ? 30_000 : false
}

export function kronosComparisonRefetchInterval(comparison: KronosComparison | undefined): number | false {
  if (isKronosComparisonActive(comparison)) return 1_500

  const awaitingEvaluation = comparison?.trials.some(trial => (
    trial.model === 'base' && trial.state === 'complete'
    && !trial.error
    && trial.evaluation !== null
    && trial.evaluation !== undefined
    && PENDING_EVALUATION_STATES.has(trial.evaluation.state)
  ))
  return awaitingEvaluation ? 60_000 : false
}

export function kronosSummaryRefetchInterval(
  summary: KronosComparisonSummary | undefined,
  rollingEnabled = false,
): number | false {
  if (rollingEnabled) return 60_000
  const hasIncompleteOrigins = summary?.groups.some(group => (
    group.model === 'base' && (group.complete_origins < group.origins || group.matched < group.expected)
  ))
  return hasIncompleteOrigins ? 60_000 : false
}

export function kronosRollingRefetchInterval(status: KronosRollingStatus | undefined, pageVisible: boolean): number | false {
  return pageVisible && status?.enabled ? 10_000 : false
}
