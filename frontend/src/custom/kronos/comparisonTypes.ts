import { parseKronosEvaluation, type KronosEvaluation } from './evaluationTypes'
import {
  parseKronosJob,
  type KronosJob,
  type KronosModel,
  type KronosPeriod,
  isValidKronosHorizon,
} from './types'

export type KronosComparisonState =
  | 'preparing' | 'running' | 'complete' | 'partial' | 'failed' | 'cancelled' | 'interrupted'
export type KronosComparisonTrialState = 'waiting' | 'preparing' | 'running' | 'complete' | 'failed' | 'cancelled' | 'skipped'
export type KronosCandidateState = 'registered' | 'retired'

export interface KronosComparisonRequest {
  symbol: string
  name: string
  period: KronosPeriod
  models: KronosModel[]
  lookbacks: number[]
  horizon: number
  samples: 1 | 3 | 5
}

export interface KronosComparisonSnapshot {
  sha256: string
  input_as_of: string
  price_basis: string
  history_count: number
  future_times: string[]
  captured_at: string
  implementation_sha256: string
}

export interface KronosComparisonTrial {
  id: string
  model: KronosModel
  lookback: number
  state: KronosComparisonTrialState
  job_id: string | null
  error: string | null
  completed_at: string | null
  timely: boolean | null
  job?: KronosJob | null
  evaluation?: KronosEvaluation | null
}

export interface KronosComparison {
  id: string
  state: KronosComparisonState
  request: KronosComparisonRequest
  created_at: string
  updated_at: string
  error: string | null
  snapshot: KronosComparisonSnapshot | null
  trials: KronosComparisonTrial[]
}

export interface KronosComparisonList {
  items: KronosComparison[]
  unavailable: number | null
}

export interface KronosScoreGroup {
  id: string
  symbol?: string
  model: KronosModel
  lookback: number
  period: KronosPeriod
  horizon: number
  samples: 1 | 3 | 5
  origins: number
  complete_origins: number
  timely_origins: number
  matched: number
  expected: number
  coverage: number | null
  mae: number | null
  mape: number | null
  direction_accuracy: number | null
  baseline_mae: number | null
  baseline_skill: number | null
  latency_seconds: number | null
  peak_mb: number | null
  eligible_for_ranking: false
  reason: string
}

export interface KronosComparisonSummary {
  groups: KronosScoreGroup[]
  batches: number
  legacy_jobs: number
  training_enabled: false
  automatic_promotion: false
}

export interface KronosBaseline {
  id: string
  label: string
  sha256: string
  installed: boolean
  verification: 'configured'
  kind: 'baseline'
}

export interface KronosCandidate {
  id: string
  label: string
  model: KronosModel
  lookback: number
  horizon: number
  samples: 1 | 3 | 5
  period: KronosPeriod
  model_sha256: string
  tokenizer_sha256: string
  seed: number | null
  source_comparison_id: string
  source_trial_id: string
  created_at: string
  state: KronosCandidateState
}

export interface KronosCreateCandidateRequest {
  comparison_id: string
  trial_id: string
  label: string
}

export interface KronosCandidatesResponse {
  baselines: KronosBaseline[]
  items: KronosCandidate[]
  training_enabled: false
  automatic_promotion: false
}

const MODELS = new Set<KronosModel>(['small', 'base'])
const PERIODS = new Set<KronosPeriod>(['daily', '1m'])
const COMPARISON_STATES = new Set<KronosComparisonState>([
  'preparing', 'running', 'complete', 'partial', 'failed', 'cancelled', 'interrupted',
])
const TRIAL_STATES = new Set<KronosComparisonTrialState>([
  'waiting', 'preparing', 'running', 'complete', 'failed', 'cancelled', 'skipped',
])
const LOOKBACKS = new Set([60, 120, 240, 400, 512])
const MAX_COMPARISON_RECORDS = 2500
const SAMPLE_COUNTS = new Set([1, 3, 5])
const SHA256 = /^[a-f\d]{64}$/i

export function effectiveKronosLookbacks(lookbacks: number[], period: KronosPeriod): number[] {
  const allowed = new Set<number>([...LOOKBACKS].filter(value => period === 'daily' || value <= 240))
  return [...new Set(lookbacks.filter(value => allowed.has(value)))].slice(0, 3)
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`Kronos ${label}响应格式无效`)
  }
  return value as Record<string, unknown>
}

function text(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`Kronos ${label}响应格式无效`)
  return value
}

function nullableText(value: unknown, label: string): string | null {
  return value === null ? null : text(value, label)
}

function nonnegativeInteger(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    throw new Error(`Kronos ${label}必须为非负整数`)
  }
  return value
}

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`Kronos ${label}格式无效`)
  return value
}

function nullableMetric(value: unknown, label: string, minimum = 0, maximum = Number.POSITIVE_INFINITY): number | null {
  if (value === null) return null
  const number = finiteNumber(value, label)
  if (number < minimum || number > maximum) throw new Error(`Kronos ${label}超出有效范围`)
  return number
}

function hash(value: unknown, label: string): string {
  const result = text(value, label)
  if (!SHA256.test(result)) throw new Error(`Kronos ${label}必须为SHA-256`)
  return result
}

function parseModel(value: unknown, label: string): KronosModel {
  if (typeof value !== 'string' || !MODELS.has(value as KronosModel)) throw new Error(`Kronos ${label}无效`)
  return value as KronosModel
}

function parsePeriod(value: unknown, label: string): KronosPeriod {
  if (typeof value !== 'string' || !PERIODS.has(value as KronosPeriod)) throw new Error(`Kronos ${label}无效`)
  return value as KronosPeriod
}

function parseHorizon(value: unknown, label: string, period: KronosPeriod): number {
  if (typeof value !== 'number' || !isValidKronosHorizon(period, value)) throw new Error(`Kronos ${label}无效`)
  return value
}

function parseSamples(value: unknown, label: string): 1 | 3 | 5 {
  if (typeof value !== 'number' || !SAMPLE_COUNTS.has(value)) throw new Error(`Kronos ${label}无效`)
  return value as 1 | 3 | 5
}

export function parseKronosComparisonRequest(value: unknown): KronosComparisonRequest {
  const input = record(value, '比较请求')
  const period = parsePeriod(input.period, '比较周期')
  if (!Array.isArray(input.models) || input.models.length < 1 || input.models.length > 2) {
    throw new Error('Kronos 比较模型数量必须为1到2个')
  }
  const models = input.models.map((model, index) => parseModel(model, `models[${index}]`))
  if (new Set(models).size !== models.length) throw new Error('Kronos 比较模型不能重复')
  if (!Array.isArray(input.lookbacks) || input.lookbacks.length < 1 || input.lookbacks.length > 3) {
    throw new Error('Kronos 比较窗口数量必须为1到3个')
  }
  const lookbacks = input.lookbacks.map((lookback, index) => {
    if (typeof lookback !== 'number' || !LOOKBACKS.has(lookback) || (period === '1m' && lookback > 240)) {
      throw new Error(`Kronos lookbacks[${index}]无效`)
    }
    return lookback
  })
  if (new Set(lookbacks).size !== lookbacks.length) throw new Error('Kronos 比较窗口不能重复')
  if (models.length * lookbacks.length > 6) throw new Error('Kronos 比较配置数量不能超过6组')
  return {
    symbol: text(input.symbol, '比较标的'),
    name: text(input.name, '比较名称'),
    period,
    models,
    lookbacks,
    horizon: parseHorizon(input.horizon, '比较步数', period),
    samples: parseSamples(input.samples, '比较样本数'),
  }
}

function parseSnapshot(value: unknown): KronosComparisonSnapshot | null {
  if (value === null) return null
  const input = record(value, '快照')
  if (!Array.isArray(input.future_times)) throw new Error('Kronos 快照future_times格式无效')
  return {
    sha256: hash(input.sha256, '快照sha256'),
    input_as_of: text(input.input_as_of, '快照input_as_of'),
    price_basis: text(input.price_basis, '快照price_basis'),
    history_count: nonnegativeInteger(input.history_count, '快照history_count'),
    future_times: input.future_times.map((time, index) => text(time, `快照future_times[${index}]`)),
    captured_at: text(input.captured_at, '快照captured_at'),
    implementation_sha256: hash(input.implementation_sha256, '快照implementation_sha256'),
  }
}

function parseTrial(value: unknown, request: KronosComparisonRequest): KronosComparisonTrial {
  const input = record(value, 'trial')
  const model = parseModel(input.model, 'trial.model')
  const lookback = nonnegativeInteger(input.lookback, 'trial.lookback')
  if (!request.models.includes(model) || !request.lookbacks.includes(lookback)
    || (request.period === '1m' && lookback > 240)) {
    throw new Error('Kronos trial配置与比较请求不匹配')
  }
  if (typeof input.state !== 'string' || !TRIAL_STATES.has(input.state as KronosComparisonTrialState)) {
    throw new Error('Kronos trial.state无效')
  }
  if (input.timely !== null && typeof input.timely !== 'boolean') throw new Error('Kronos trial.timely格式无效')

  const jobId = nullableText(input.job_id, 'trial.job_id')
  const trial: KronosComparisonTrial = {
    id: text(input.id, 'trial.id'),
    model,
    lookback,
    state: input.state as KronosComparisonTrialState,
    job_id: jobId,
    error: nullableText(input.error, 'trial.error'),
    completed_at: nullableText(input.completed_at, 'trial.completed_at'),
    timely: input.timely,
  }

  if ('job' in input) {
    trial.job = input.job === null ? null : parseKronosJob(input.job)
    if (trial.job && trial.job.id !== jobId) throw new Error('Kronos trial.job与job_id不匹配')
    if (trial.job && (trial.job.request.model !== model || trial.job.request.lookback !== lookback
      || trial.job.request.period !== request.period || trial.job.request.symbol !== request.symbol)) {
      throw new Error('Kronos trial.job配置与比较请求不匹配')
    }
  }
  if ('evaluation' in input) {
    trial.evaluation = input.evaluation === null ? null : parseKronosEvaluation(input.evaluation, jobId ?? undefined)
  }
  return trial
}

export function parseKronosComparison(value: unknown): KronosComparison {
  const input = record(value, '比较')
  if (typeof input.state !== 'string' || !COMPARISON_STATES.has(input.state as KronosComparisonState)) {
    throw new Error('Kronos 比较state无效')
  }
  const request = parseKronosComparisonRequest(input.request)
  if (!Array.isArray(input.trials)) throw new Error('Kronos 比较trials格式无效')
  const trials = input.trials.map(trial => parseTrial(trial, request))
  if (new Set(trials.map(trial => trial.id)).size !== trials.length) throw new Error('Kronos 比较trial ID重复')
  const combinations = trials.map(trial => `${trial.model}:${trial.lookback}`)
  if (new Set(combinations).size !== combinations.length || trials.length > request.models.length * request.lookbacks.length) {
    throw new Error('Kronos 比较trial配置重复或超出请求范围')
  }
  return {
    id: text(input.id, '比较.id'),
    state: input.state as KronosComparisonState,
    request,
    created_at: text(input.created_at, '比较.created_at'),
    updated_at: text(input.updated_at, '比较.updated_at'),
    error: nullableText(input.error, '比较.error'),
    snapshot: parseSnapshot(input.snapshot),
    trials,
  }
}

export function parseKronosComparisonList(value: unknown): KronosComparisonList {
  const input = record(value, '比较列表')
  if (!Array.isArray(input.items) || input.items.length > MAX_COMPARISON_RECORDS) throw new Error('Kronos 比较列表items格式无效')
  const unavailable = input.unavailable === undefined ? null : nonnegativeInteger(input.unavailable, '比较列表unavailable')
  return { items: input.items.map(parseKronosComparison), unavailable }
}

function parseScoreGroup(value: unknown): KronosScoreGroup {
  const input = record(value, '成绩分组')
  if (input.eligible_for_ranking !== false) throw new Error('Kronos 成绩分组不能参与排名')
  const period = parsePeriod(input.period, '成绩分组period')
  const lookback = nonnegativeInteger(input.lookback, '成绩分组lookback')
  if (!LOOKBACKS.has(lookback) || (period === '1m' && lookback > 240)) throw new Error('Kronos 成绩分组lookback无效')
  const origins = nonnegativeInteger(input.origins, '成绩分组origins')
  const completeOrigins = nonnegativeInteger(input.complete_origins, '成绩分组complete_origins')
  const timelyOrigins = nonnegativeInteger(input.timely_origins, '成绩分组timely_origins')
  const matched = nonnegativeInteger(input.matched, '成绩分组matched')
  const expected = nonnegativeInteger(input.expected, '成绩分组expected')
  if (completeOrigins > origins || timelyOrigins > completeOrigins || matched > expected) {
    throw new Error('Kronos 成绩分组计数不一致')
  }
  return {
    id: text(input.id, '成绩分组.id'),
    ...(input.symbol === undefined ? {} : { symbol: text(input.symbol, '成绩分组.symbol') }),
    model: parseModel(input.model, '成绩分组.model'),
    lookback,
    period,
    horizon: parseHorizon(input.horizon, '成绩分组.horizon', period),
    samples: parseSamples(input.samples, '成绩分组.samples'),
    origins,
    complete_origins: completeOrigins,
    timely_origins: timelyOrigins,
    matched,
    expected,
    coverage: nullableMetric(input.coverage, '成绩分组.coverage', 0, 1),
    mae: nullableMetric(input.mae, '成绩分组.mae'),
    mape: nullableMetric(input.mape, '成绩分组.mape'),
    direction_accuracy: nullableMetric(input.direction_accuracy, '成绩分组.direction_accuracy', 0, 1),
    baseline_mae: nullableMetric(input.baseline_mae, '成绩分组.baseline_mae'),
    baseline_skill: nullableMetric(input.baseline_skill, '成绩分组.baseline_skill', Number.NEGATIVE_INFINITY),
    latency_seconds: nullableMetric(input.latency_seconds, '成绩分组.latency_seconds'),
    peak_mb: nullableMetric(input.peak_mb, '成绩分组.peak_mb'),
    eligible_for_ranking: false,
    reason: typeof input.reason === 'string' ? input.reason : '',
  }
}

export function parseKronosComparisonSummary(value: unknown): KronosComparisonSummary {
  const input = record(value, '成绩汇总')
  if (input.training_enabled !== false || input.automatic_promotion !== false) {
    throw new Error('Kronos 当前不允许训练或自动晋级')
  }
  if (!Array.isArray(input.groups)) throw new Error('Kronos 成绩汇总groups格式无效')
  const groups = input.groups.map(parseScoreGroup)
  if (new Set(groups.map(group => group.id)).size !== groups.length) throw new Error('Kronos 成绩分组ID重复')
  return {
    groups,
    batches: nonnegativeInteger(input.batches, '成绩汇总batches'),
    legacy_jobs: nonnegativeInteger(input.legacy_jobs, '成绩汇总legacy_jobs'),
    training_enabled: false,
    automatic_promotion: false,
  }
}

function parseBaseline(value: unknown): KronosBaseline {
  const input = record(value, 'baseline')
  if (input.installed !== true && input.installed !== false) throw new Error('Kronos baseline.installed格式无效')
  if (input.verification !== 'configured' || input.kind !== 'baseline') throw new Error('Kronos baseline类型无效')
  return {
    id: text(input.id, 'baseline.id'),
    label: text(input.label, 'baseline.label'),
    sha256: hash(input.sha256, 'baseline.sha256'),
    installed: input.installed,
    verification: 'configured',
    kind: 'baseline',
  }
}

export function parseKronosCandidate(value: unknown): KronosCandidate {
  const input = record(value, '候选配置')
  if (input.state !== 'registered' && input.state !== 'retired') throw new Error('Kronos 候选state无效')
  const seed = input.seed === null ? null : nonnegativeInteger(input.seed, '候选seed')
  const period = parsePeriod(input.period, '候选period')
  const lookback = nonnegativeInteger(input.lookback, '候选lookback')
  if (!LOOKBACKS.has(lookback) || (period === '1m' && lookback > 240)) throw new Error('Kronos 候选lookback无效')
  return {
    id: text(input.id, '候选.id'),
    label: text(input.label, '候选.label'),
    model: parseModel(input.model, '候选.model'),
    lookback,
    horizon: parseHorizon(input.horizon, '候选.horizon', period),
    samples: parseSamples(input.samples, '候选.samples'),
    period,
    model_sha256: hash(input.model_sha256, '候选.model_sha256'),
    tokenizer_sha256: hash(input.tokenizer_sha256, '候选.tokenizer_sha256'),
    seed,
    source_comparison_id: text(input.source_comparison_id, '候选.source_comparison_id'),
    source_trial_id: text(input.source_trial_id, '候选.source_trial_id'),
    created_at: text(input.created_at, '候选.created_at'),
    state: input.state,
  }
}

export function parseKronosCandidatesResponse(value: unknown): KronosCandidatesResponse {
  const input = record(value, '候选列表')
  if (input.training_enabled !== false || input.automatic_promotion !== false) {
    throw new Error('Kronos 当前不允许训练或自动晋级')
  }
  if (!Array.isArray(input.baselines) || !Array.isArray(input.items)) throw new Error('Kronos 候选列表格式无效')
  return {
    baselines: input.baselines.map(parseBaseline),
    items: input.items.map(parseKronosCandidate),
    training_enabled: false,
    automatic_promotion: false,
  }
}

export function isKronosComparisonActive(comparison: KronosComparison | undefined): boolean {
  return comparison?.state === 'preparing' || comparison?.state === 'running'
}
