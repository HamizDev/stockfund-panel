import { describe, expect, it } from 'vitest'
import {
  parseKronosCandidatesResponse,
  parseKronosComparisonList,
  parseKronosComparisonRequest,
  parseKronosComparisonSummary,
  effectiveKronosLookbacks,
} from './comparisonTypes'

const comparisonRequest = {
  symbol: '600000.SH',
  name: '浦发银行',
  period: 'daily',
  models: ['small', 'base'],
  lookbacks: [60, 120],
  horizon: 5,
  samples: 1,
}

const summaryComparison = {
  id: 'comparison-1',
  state: 'partial',
  request: comparisonRequest,
  created_at: '2026-10-27T01:00:00Z',
  updated_at: '2026-10-27T01:01:00Z',
  error: null,
  snapshot: null,
  trials: [
    {
      id: 'trial-1', model: 'small', lookback: 60, state: 'complete', job_id: 'job-1',
      error: null, completed_at: '2026-10-27T01:01:00Z', timely: false, output_sha256: 'a'.repeat(64),
    },
    {
      id: 'trial-2', model: 'base', lookback: 120, state: 'running', job_id: null,
      error: null, completed_at: null, timely: null,
    },
  ],
}

function scoreGroup(overrides: Record<string, unknown> = {}) {
  return {
    id: 'small-daily-60-5-1',
    symbol: '600000.SH',
    model: 'small',
    lookback: 60,
    period: 'daily',
    horizon: 5,
    samples: 1,
    origins: 2,
    complete_origins: 1,
    timely_origins: 0,
    matched: 3,
    expected: 10,
    coverage: 0.3,
    mae: null,
    mape: null,
    direction_accuracy: null,
    baseline_mae: null,
    baseline_skill: null,
    latency_seconds: 2.4,
    peak_mb: 512,
    eligible_for_ranking: false,
    reason: '等待其余批次完整核对',
    ...overrides,
  }
}

describe('Kronos comparison contracts', () => {
  it('filters period-invalid windows and caps selected windows at three', () => {
    expect(effectiveKronosLookbacks([60, 120, 400], '1m')).toEqual([60, 120])
    expect(effectiveKronosLookbacks([60, 60, 120, 240, 400], '1m')).toEqual([60, 120, 240])
    expect(effectiveKronosLookbacks([60, 120, 240, 400, 512], 'daily')).toEqual([60, 120, 240])
  })

  it('parses list summaries without per-trial job or evaluation payloads', () => {
    const parsed = parseKronosComparisonList({ items: [summaryComparison], unavailable: 2 })

    expect(parsed.items[0].trials[0].job).toBeUndefined()
    expect(parsed.items[0].trials[0].timely).toBe(false)
    expect(parsed.items[0].request.models).toEqual(['small', 'base'])
    expect(parsed.unavailable).toBe(2)
  })

  it('rejects duplicate configurations and more than three lookback windows', () => {
    expect(() => parseKronosComparisonRequest({
      ...comparisonRequest,
      lookbacks: [60, 60],
    })).toThrow('窗口不能重复')
    expect(() => parseKronosComparisonRequest({
      ...comparisonRequest,
      lookbacks: [60, 120, 240, 400],
    })).toThrow('1到3个')
  })

  it('accepts only integer minute horizons from 1 to 20 while keeping daily horizons unchanged', () => {
    expect(parseKronosComparisonRequest({
      ...comparisonRequest,
      period: '1m', models: ['base'], lookbacks: [120, 240], horizon: 1,
    }).horizon).toBe(1)
    expect(parseKronosComparisonRequest({
      ...comparisonRequest,
      period: '1m', models: ['base'], lookbacks: [120, 240], horizon: 20,
    }).horizon).toBe(20)
    for (const horizon of [0, 21, 1.5]) {
      expect(() => parseKronosComparisonRequest({
        ...comparisonRequest,
        period: '1m', models: ['base'], lookbacks: [120, 240], horizon,
      })).toThrow('比较步数无效')
    }
    expect(() => parseKronosComparisonRequest({ ...comparisonRequest, horizon: 1 }))
      .toThrow('比较步数无效')
  })

  it('keeps partial score groups as coverage rows with null metrics and no ranking eligibility', () => {
    const summary = parseKronosComparisonSummary({
      groups: [scoreGroup()],
      batches: 2,
      legacy_jobs: 4,
      training_enabled: false,
      automatic_promotion: false,
    })

    expect(summary.groups[0]).toMatchObject({
      complete_origins: 1,
      matched: 3,
      expected: 10,
      coverage: 0.3,
      mae: null,
      eligible_for_ranking: false,
    })
  })

  it('fails closed if the summary claims ranking or training is enabled', () => {
    expect(() => parseKronosComparisonSummary({
      groups: [scoreGroup({ eligible_for_ranking: true })],
      batches: 1,
      legacy_jobs: 0,
      training_enabled: false,
      automatic_promotion: false,
    })).toThrow('不能参与排名')
    expect(() => parseKronosComparisonSummary({
      groups: [], batches: 0, legacy_jobs: 0, training_enabled: true, automatic_promotion: false,
    })).toThrow('不允许训练或自动晋级')
  })

  it('parses registered candidates with the fixed integer seed', () => {
    const hash = 'a'.repeat(64)
    const candidates = parseKronosCandidatesResponse({
      baselines: [{
        id: 'small-baseline', label: 'Small 基线', sha256: hash,
        installed: true, verification: 'configured', kind: 'baseline',
      }],
      items: [{
        id: 'candidate-1', label: '小窗口', model: 'small', lookback: 120, horizon: 10,
        samples: 3, period: 'daily', model_sha256: hash, tokenizer_sha256: hash, seed: 42,
        source_comparison_id: 'comparison-1', source_trial_id: 'trial-1', schema_version: 1,
        created_at: '2026-10-27T01:01:00Z', state: 'registered',
      }],
      training_enabled: false,
      automatic_promotion: false,
    })

    expect(candidates.items[0]).toMatchObject({ seed: 42, state: 'registered', model: 'small' })
    expect(candidates.baselines[0].verification).toBe('configured')
  })
})
