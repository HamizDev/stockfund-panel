import { describe, expect, it } from 'vitest'
import type { KronosComparison, KronosComparisonSummary, KronosComparisonTrial } from './comparisonTypes'
import { kronosComparisonRefetchInterval, kronosSummaryRefetchInterval } from './polling'

function comparison(state: KronosComparison['state'], trials: KronosComparisonTrial[]): KronosComparison {
  return { state, trials } as KronosComparison
}

function completedTrial(evaluationState: 'pending' | 'partial' | 'complete' | 'unavailable', error: string | null = null): KronosComparisonTrial {
  return {
    model: 'base',
    state: 'complete',
    error,
    evaluation: { state: evaluationState },
  } as KronosComparisonTrial
}

function summary(completeOrigins: number, origins: number, matched: number, expected: number): KronosComparisonSummary {
  return {
    groups: [{ model: 'base', complete_origins: completeOrigins, origins, matched, expected }],
  } as KronosComparisonSummary
}

describe('Kronos polling intervals', () => {
  it('polls active comparisons frequently and stops after completed evaluation', () => {
    expect(kronosComparisonRefetchInterval(comparison('running', []))).toBe(1_500)
    expect(kronosComparisonRefetchInterval(comparison('complete', [completedTrial('complete')]))).toBe(false)
    expect(kronosComparisonRefetchInterval(undefined)).toBe(false)
  })

  it.each(['pending', 'partial', 'unavailable'] as const)(
    'checks completed predictions again while evaluation is %s',
    state => {
      expect(kronosComparisonRefetchInterval(comparison('complete', [completedTrial(state)]))).toBe(60_000)
    },
  )

  it('does not poll a trial with an error or one without an unfinished evaluation', () => {
    expect(kronosComparisonRefetchInterval(comparison('complete', [completedTrial('pending', 'invalid result')]))).toBe(false)
    expect(kronosComparisonRefetchInterval(comparison('complete', [{ state: 'complete', error: null } as KronosComparisonTrial]))).toBe(false)
  })

  it('polls incomplete summary groups once per minute and stops at full coverage', () => {
    expect(kronosSummaryRefetchInterval(summary(1, 2, 8, 10))).toBe(60_000)
    expect(kronosSummaryRefetchInterval(summary(2, 2, 8, 10))).toBe(60_000)
    expect(kronosSummaryRefetchInterval(summary(2, 2, 10, 10))).toBe(false)
    expect(kronosSummaryRefetchInterval(undefined)).toBe(false)
  })
})
