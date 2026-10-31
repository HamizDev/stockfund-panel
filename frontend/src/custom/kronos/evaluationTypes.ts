export type KronosEvaluationState = 'pending' | 'partial' | 'complete' | 'unavailable' | 'not_applicable'
export type KronosEvaluationPointStatus = 'pending' | 'matched' | 'unavailable'

export interface KronosEvaluationPoint {
  time: string
  predicted_close: number
  actual_close: number | null
  status: KronosEvaluationPointStatus
  error: number | null
  direction_match: boolean | null
}

export interface KronosEvaluationMetrics {
  mae: number | null
  rmse: number | null
  mape: number | null
  baseline_mae: number | null
  direction_matches: number
  direction_total: number
  direction_accuracy: number | null
  flat_actual_count: number
}

export interface KronosEvaluation {
  schema_version: 1
  job_id: string
  state: KronosEvaluationState
  expected: number
  matched: number
  pending: number
  missing: number
  evaluated_at: string | null
  message: string
  reference_close: number | null
  forecast_sha256: string | null
  points: KronosEvaluationPoint[]
  metrics: KronosEvaluationMetrics
}

const EVALUATION_STATES = new Set<KronosEvaluationState>([
  'pending', 'partial', 'complete', 'unavailable', 'not_applicable',
])
const POINT_STATUSES = new Set<KronosEvaluationPointStatus>(['pending', 'matched', 'unavailable'])

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`Kronos 评估${label}格式无效`)
  }
  return value as Record<string, unknown>
}

function text(value: unknown, label: string, allowEmpty = false): string {
  if (typeof value !== 'string' || (!allowEmpty && value.trim().length === 0)) {
    throw new Error(`Kronos 评估${label}格式无效`)
  }
  return value
}

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`Kronos 评估${label}格式无效`)
  }
  return value
}

function nonnegativeInteger(value: unknown, label: string): number {
  const number = finiteNumber(value, label)
  if (!Number.isInteger(number) || number < 0) {
    throw new Error(`Kronos 评估${label}必须为非负整数`)
  }
  return number
}

function positiveNumber(value: unknown, label: string): number {
  const number = finiteNumber(value, label)
  if (number <= 0) throw new Error(`Kronos 评估${label}必须为正数`)
  return number
}

function nullableNumber(value: unknown, label: string): number | null {
  return value === null ? null : finiteNumber(value, label)
}

function nullableNonnegativeNumber(value: unknown, label: string): number | null {
  const number = nullableNumber(value, label)
  if (number !== null && number < 0) throw new Error(`Kronos 评估${label}不能为负数`)
  return number
}

function parsePoint(value: unknown, index: number): KronosEvaluationPoint {
  const label = `points[${index}]`
  const row = record(value, label)
  if (typeof row.status !== 'string' || !POINT_STATUSES.has(row.status as KronosEvaluationPointStatus)) {
    throw new Error(`Kronos 评估${label}.status无效`)
  }

  const status = row.status as KronosEvaluationPointStatus
  const actualClose = row.actual_close === null ? null : positiveNumber(row.actual_close, `${label}.actual_close`)
  const error = nullableNumber(row.error, `${label}.error`)
  const directionMatch = row.direction_match === null || typeof row.direction_match === 'boolean'
    ? row.direction_match as boolean | null
    : (() => { throw new Error(`Kronos 评估${label}.direction_match格式无效`) })()

  if (status === 'matched') {
    if (actualClose === null || error === null) throw new Error(`Kronos 评估${label}匹配状态缺少真实价格或误差`)
  } else if (actualClose !== null || error !== null || directionMatch !== null) {
    throw new Error(`Kronos 评估${label}未匹配点不能包含真实价格、误差或方向结果`)
  }

  return {
    time: text(row.time, `${label}.time`),
    predicted_close: positiveNumber(row.predicted_close, `${label}.predicted_close`),
    actual_close: actualClose,
    status,
    error,
    direction_match: directionMatch,
  }
}

function parseMetrics(value: unknown, matched: number, referenceClose: number | null, points: KronosEvaluationPoint[]): KronosEvaluationMetrics {
  const input = record(value, 'metrics')
  const mae = nullableNonnegativeNumber(input.mae, 'metrics.mae')
  const rmse = nullableNonnegativeNumber(input.rmse, 'metrics.rmse')
  const mape = nullableNonnegativeNumber(input.mape, 'metrics.mape')
  const baselineMae = nullableNonnegativeNumber(input.baseline_mae, 'metrics.baseline_mae')
  const directionMatches = nonnegativeInteger(input.direction_matches, 'metrics.direction_matches')
  const directionTotal = nonnegativeInteger(input.direction_total, 'metrics.direction_total')
  const directionAccuracy = nullableNumber(input.direction_accuracy, 'metrics.direction_accuracy')
  const flatActualCount = nonnegativeInteger(input.flat_actual_count, 'metrics.flat_actual_count')

  if (directionMatches > directionTotal || directionTotal > matched || flatActualCount > matched) {
    throw new Error('Kronos 评估metrics方向计数超出已匹配点范围')
  }
  if (directionAccuracy !== null && (directionAccuracy < 0 || directionAccuracy > 1)) {
    throw new Error('Kronos 评估metrics.direction_accuracy必须为0到1之间的比例')
  }
  if ((directionTotal === 0 && directionAccuracy !== null)
    || (directionTotal > 0 && (directionAccuracy === null
      || Math.abs(directionAccuracy - directionMatches / directionTotal) > 1e-9))) {
    throw new Error('Kronos 评估metrics.direction_accuracy分母不一致')
  }
  if (points.filter(point => point.direction_match !== null).length !== directionTotal
    || points.filter(point => point.direction_match === true).length !== directionMatches) {
    throw new Error('Kronos 评估方向点数与metrics计数不一致')
  }
  if (matched === 0 && [mae, rmse, mape, baselineMae].some(metric => metric !== null)) {
    throw new Error('Kronos 无已匹配点时评估误差必须为空')
  }
  if ((referenceClose === null && (baselineMae !== null || directionTotal !== 0))
    || (referenceClose !== null && points.some(point => point.status === 'matched' && point.direction_match === null))) {
    throw new Error('Kronos 评估方向和基准指标缺少参考收盘价')
  }

  return {
    mae,
    rmse,
    mape,
    baseline_mae: baselineMae,
    direction_matches: directionMatches,
    direction_total: directionTotal,
    direction_accuracy: directionAccuracy,
    flat_actual_count: flatActualCount,
  }
}

/** Parse the evaluation contract and optionally bind it to the requested job. */
export function parseKronosEvaluation(value: unknown, expectedJobId?: string): KronosEvaluation {
  const input = record(value, '响应')
  if (input.schema_version !== 1) throw new Error('Kronos 评估schema_version不受支持')
  if (typeof input.state !== 'string' || !EVALUATION_STATES.has(input.state as KronosEvaluationState)) {
    throw new Error('Kronos 评估state无效')
  }
  if (input.evaluated_at !== null && (typeof input.evaluated_at !== 'string' || input.evaluated_at.trim().length === 0)) {
    throw new Error('Kronos 评估evaluated_at格式无效')
  }
  if (input.forecast_sha256 !== null
    && (typeof input.forecast_sha256 !== 'string' || !/^[a-f\d]{64}$/i.test(input.forecast_sha256))) {
    throw new Error('Kronos 评估forecast_sha256格式无效')
  }

  const jobId = text(input.job_id, 'job_id')
  if (expectedJobId !== undefined && jobId !== expectedJobId) {
    throw new Error('Kronos 评估job_id与请求不匹配')
  }
  const expected = nonnegativeInteger(input.expected, 'expected')
  const matched = nonnegativeInteger(input.matched, 'matched')
  const pending = nonnegativeInteger(input.pending, 'pending')
  const missing = nonnegativeInteger(input.missing, 'missing')
  if ([matched, pending, missing].some(count => count > expected) || matched + pending + missing !== expected) {
    throw new Error('Kronos 评估计数与expected不一致')
  }
  if (!Array.isArray(input.points) || input.points.length !== expected) {
    throw new Error('Kronos 评估points数量与expected不一致')
  }

  const points = input.points.map(parsePoint)
  const pointCounts = {
    matched: points.filter(point => point.status === 'matched').length,
    pending: points.filter(point => point.status === 'pending').length,
    missing: points.filter(point => point.status === 'unavailable').length,
  }
  if (pointCounts.matched !== matched || pointCounts.pending !== pending || pointCounts.missing !== missing) {
    throw new Error('Kronos 评估points状态与计数不一致')
  }
  if (input.state === 'complete' && pending !== 0) {
    throw new Error('Kronos complete评估仍包含待核对点')
  }

  const referenceClose = input.reference_close === null ? null : positiveNumber(input.reference_close, 'reference_close')
  const metrics = parseMetrics(input.metrics, matched, referenceClose, points)

  return {
    schema_version: 1,
    job_id: jobId,
    state: input.state as KronosEvaluationState,
    expected,
    matched,
    pending,
    missing,
    evaluated_at: input.evaluated_at as string | null,
    message: text(input.message, 'message', true),
    reference_close: referenceClose,
    forecast_sha256: input.forecast_sha256 as string | null,
    points,
    metrics,
  }
}
