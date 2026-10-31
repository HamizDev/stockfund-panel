import { describe, expect, it } from 'vitest'
import { formatKronosEvaluationMetric } from './KronosEvaluation'

describe('Kronos evaluation error formatting', () => {
  it('formats floating point error values to six decimal places', () => {
    expect(formatKronosEvaluationMetric(-0.010000000000000009)).toBe('-0.010000')
    expect(formatKronosEvaluationMetric(null)).toBe('—')
  })
})
