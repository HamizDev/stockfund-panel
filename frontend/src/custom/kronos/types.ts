export type KronosModel = 'small' | 'base'
export type KronosPeriod = 'daily' | '1m'
export type KronosJobState = 'preparing' | 'running' | 'complete' | 'failed' | 'cancelled'
export type KronosForecastMode = 'ohlc' | 'close_only'

export const KRONOS_CLOSE_ONLY_WARNING = '仅展示收盘价预测路径'

export interface KronosJobRequest {
  symbol: string
  name: string
  model: KronosModel
  period: KronosPeriod
  lookback: number
  horizon: number
  samples: number
}

export interface KronosStatus {
  ready: boolean
  busy: boolean
  active_job_id: string | null
  device: 'cuda'
  models: KronosModel[]
  message: string
}

export type KronosRollingState = 'idle' | 'waiting' | 'running' | 'paused' | 'complete' | 'error' | 'interrupted'

export interface KronosRollingRequest {
  symbol: string
  name: string
  targets?: Array<{ symbol: string; name: string }>
  period: '1m'
  models: ['base']
  lookbacks: [120, 240]
  horizon: 20
  samples: 3
}

export interface KronosRollingRun {
  scheduled_at: string
  comparison_id: string | null
  state: string
  symbol?: string
  name?: string
  started_at?: string | null
}

export interface KronosRollingTargetProgress {
  symbol: string
  name: string
  state: string
  last_batch_id: string | null
  last_started_at: string | null
  completed_batches: number
}

export interface KronosRollingStatus {
  id: string | null
  state: KronosRollingState
  enabled: boolean
  session_date: string | null
  request: KronosRollingRequest | null
  next_run_at: string | null
  last_batch_id: string | null
  completed_rounds: number
  message: string
  runs: KronosRollingRun[]
  auto_daily: boolean
  next_target_at: string | null
  active_symbol: string | null
  target_progress: KronosRollingTargetProgress[]
}

export interface KronosBar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume?: number | null
  amount?: number | null
}

export interface KronosCloseOnlyBar {
  time: string
  close: number
}

export type KronosForecastBar = KronosBar | KronosCloseOnlyBar

export interface KronosResult {
  symbol: string
  name: string
  model: KronosModel
  period: KronosPeriod
  forecast_mode: KronosForecastMode
  history: KronosBar[]
  forecast: KronosForecastBar[]
  prev_close?: number | null
  prev_close_date?: string | null
  chart_context?: KronosBar[]
  input_as_of: string
  requested_at: string
  price_basis: string
  warnings: string[]
  metrics: {
    elapsed_seconds: number
    cuda_peak_allocated_mb: number
    cuda_peak_reserved_mb: number
  }
}

export interface KronosJob {
  id: string
  state: KronosJobState
  request: KronosJobRequest
  requested_at: string
  message: string
  error: string | null
  result: KronosResult | null
}

const MODELS = new Set<KronosModel>(['small', 'base'])
const PERIODS = new Set<KronosPeriod>(['daily', '1m'])
const JOB_STATES = new Set<KronosJobState>(['preparing', 'running', 'complete', 'failed', 'cancelled'])
const LOOKBACKS = new Set([60, 120, 240, 400, 512])
const HORIZONS = new Set([5, 10, 20])
const SAMPLE_COUNTS = new Set([1, 3, 5])

export function isValidKronosHorizon(period: KronosPeriod, horizon: number): boolean {
  return Number.isInteger(horizon)
    && (period === '1m' ? horizon >= 1 && horizon <= 20 : HORIZONS.has(horizon))
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`Kronos ${label} 响应格式无效`)
  }
  return value as Record<string, unknown>
}

function text(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new Error(`Kronos ${label} 响应格式无效`)
  }
  return value
}

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`Kronos ${label} 响应格式无效`)
  }
  return value
}

function positiveNumber(value: unknown, label: string): number {
  const number = finiteNumber(value, label)
  if (number <= 0) throw new Error(`Kronos ${label} 必须为正数`)
  return number
}

export function parseKronosRequest(value: unknown): KronosJobRequest {
  const input = record(value, '请求')
  if (typeof input.symbol !== 'string' || input.symbol.trim().length === 0
    || typeof input.name !== 'string' || input.name.trim().length === 0
    || typeof input.model !== 'string' || !MODELS.has(input.model as KronosModel)
    || typeof input.period !== 'string' || !PERIODS.has(input.period as KronosPeriod)
    || typeof input.lookback !== 'number' || !LOOKBACKS.has(input.lookback)
    || (input.period === '1m' && typeof input.lookback === 'number' && input.lookback > 240)
    || typeof input.horizon !== 'number' || !isValidKronosHorizon(input.period as KronosPeriod, input.horizon)
    || typeof input.samples !== 'number' || !SAMPLE_COUNTS.has(input.samples)) {
    throw new Error('Kronos 请求参数无效')
  }
  return {
    symbol: input.symbol,
    name: input.name,
    model: input.model as KronosModel,
    period: input.period as KronosPeriod,
    lookback: input.lookback,
    horizon: input.horizon,
    samples: input.samples,
  }
}

const ROLLING_STATES = new Set<KronosRollingState>([
  'idle', 'waiting', 'running', 'paused', 'complete', 'error', 'interrupted',
])

function nullableText(value: unknown, label: string): string | null {
  if (value === null) return null
  return text(value, label)
}

function parseKronosRollingRequest(value: unknown): KronosRollingRequest {
  const input = record(value, '滚动请求')
  if (typeof input.symbol !== 'string' || !input.symbol.trim()
    || typeof input.name !== 'string' || !input.name.trim()
    || input.period !== '1m'
    || !Array.isArray(input.models) || input.models.length !== 1 || input.models[0] !== 'base'
    || !Array.isArray(input.lookbacks) || input.lookbacks.length !== 2
    || input.lookbacks[0] !== 120 || input.lookbacks[1] !== 240
    || input.horizon !== 20 || input.samples !== 3) {
    throw new Error('Kronos 滚动请求配置无效')
  }
  return {
    symbol: input.symbol,
    name: input.name,
    ...(input.targets === undefined ? {} : {
      targets: (() => {
        if (!Array.isArray(input.targets)) throw new Error('Kronos 滚动目标列表无效')
        return input.targets.map((item, index) => {
          const target = record(item, `滚动请求.targets[${index}]`)
          return {
            symbol: text(target.symbol, `滚动请求.targets[${index}].symbol`),
            name: text(target.name, `滚动请求.targets[${index}].name`),
          }
        })
      })(),
    }),
    period: '1m',
    models: ['base'],
    lookbacks: [120, 240],
    horizon: 20,
    samples: 3,
  }
}

export function parseKronosRollingStatus(value: unknown): KronosRollingStatus {
  const input = record(value, '滚动状态')
  const autoDaily = input.auto_daily === undefined ? false : input.auto_daily
  const targetProgress = input.target_progress === undefined ? [] : input.target_progress
  if (typeof input.state !== 'string' || !ROLLING_STATES.has(input.state as KronosRollingState)
    || typeof input.enabled !== 'boolean' || !Array.isArray(input.runs)
    || typeof input.completed_rounds !== 'number' || !Number.isInteger(input.completed_rounds)
    || input.completed_rounds < 0 || typeof input.message !== 'string'
    || typeof autoDaily !== 'boolean' || !Array.isArray(targetProgress)) {
    throw new Error('Kronos 滚动状态响应格式无效')
  }
  const optionalNullableText = (field: unknown, label: string) =>
    field === undefined ? null : nullableText(field, label)
  return {
    id: nullableText(input.id, '滚动状态.id'),
    state: input.state as KronosRollingState,
    enabled: input.enabled,
    session_date: nullableText(input.session_date, '滚动状态.session_date'),
    request: input.request === null ? null : parseKronosRollingRequest(input.request),
    next_run_at: nullableText(input.next_run_at, '滚动状态.next_run_at'),
    last_batch_id: nullableText(input.last_batch_id, '滚动状态.last_batch_id'),
    completed_rounds: input.completed_rounds,
    message: input.message,
    auto_daily: autoDaily,
    next_target_at: optionalNullableText(input.next_target_at, '滚动状态.next_target_at'),
    active_symbol: optionalNullableText(input.active_symbol, '滚动状态.active_symbol'),
    target_progress: targetProgress.map((item, index) => {
      const target = record(item, `滚动状态.target_progress[${index}]`)
      if (typeof target.completed_batches !== 'number' || !Number.isInteger(target.completed_batches)
        || target.completed_batches < 0) {
        throw new Error('Kronos 滚动目标进度响应格式无效')
      }
      return {
        symbol: text(target.symbol, `滚动状态.target_progress[${index}].symbol`),
        name: text(target.name, `滚动状态.target_progress[${index}].name`),
        state: text(target.state, `滚动状态.target_progress[${index}].state`),
        last_batch_id: optionalNullableText(target.last_batch_id, `滚动状态.target_progress[${index}].last_batch_id`),
        last_started_at: optionalNullableText(target.last_started_at, `滚动状态.target_progress[${index}].last_started_at`),
        completed_batches: target.completed_batches,
      }
    }),
    runs: input.runs.map((item, index) => {
      const run = record(item, `滚动状态.runs[${index}]`)
      return {
        scheduled_at: text(run.scheduled_at, `滚动状态.runs[${index}].scheduled_at`),
        comparison_id: nullableText(run.comparison_id, `滚动状态.runs[${index}].comparison_id`),
        state: text(run.state, `滚动状态.runs[${index}].state`),
        ...(run.symbol === undefined ? {} : { symbol: text(run.symbol, `滚动状态.runs[${index}].symbol`) }),
        ...(run.name === undefined ? {} : { name: text(run.name, `滚动状态.runs[${index}].name`) }),
        ...(run.started_at === undefined ? {} : {
          started_at: nullableText(run.started_at, `滚动状态.runs[${index}].started_at`),
        }),
      }
    }),
  }
}

export function parseKronosStatus(value: unknown): KronosStatus {
  const input = record(value, '状态')
  const models = input.models
  if (typeof input.ready !== 'boolean' || typeof input.busy !== 'boolean'
    || (input.active_job_id !== null && typeof input.active_job_id !== 'string')
    || input.device !== 'cuda' || !Array.isArray(models)
    || models.some(model => typeof model !== 'string' || !MODELS.has(model as KronosModel))) {
    throw new Error('Kronos 状态响应格式无效')
  }
  return {
    ready: input.ready,
    busy: input.busy,
    active_job_id: input.active_job_id as string | null,
    device: 'cuda',
    models: models as KronosModel[],
    message: typeof input.message === 'string' ? input.message : '',
  }
}

function parseBars(value: unknown, label: string): KronosBar[] {
  if (!Array.isArray(value)) throw new Error(`Kronos ${label} 响应格式无效`)
  return value.map((item, index) => {
    const row = record(item, `${label}[${index}]`)
    const optionalMetric = (key: 'volume' | 'amount'): number | null | undefined => {
      if (!(key in row)) return undefined
      if (row[key] === null) return null
      const metric = row[key]
      return typeof metric === 'number' && Number.isFinite(metric) && metric >= 0 ? metric : undefined
    }
    const volume = optionalMetric('volume')
    const amount = optionalMetric('amount')
    return {
      time: text(row.time, `${label}[${index}].time`),
      open: finiteNumber(row.open, `${label}[${index}].open`),
      high: finiteNumber(row.high, `${label}[${index}].high`),
      low: finiteNumber(row.low, `${label}[${index}].low`),
      close: finiteNumber(row.close, `${label}[${index}].close`),
      ...(volume !== undefined ? { volume } : {}),
      ...(amount !== undefined ? { amount } : {}),
    }
  })
}

function parseOptionalPrevClose(value: unknown): number | null | undefined {
  if (value === undefined) return undefined
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
}

function parseOptionalDate(value: unknown): string | null | undefined {
  if (value === undefined) return undefined
  return value === null ? null : typeof value === 'string' && value.trim() ? value : null
}

function parseForecastBars(value: unknown, mode: KronosForecastMode): KronosForecastBar[] {
  if (mode === 'ohlc') {
    const bars = parseBars(value, 'forecast')
    bars.forEach((bar, index) => {
      const { open, high, low, close } = bar
      if ([open, high, low, close].some(price => price <= 0)
        || high < Math.max(open, close) || low > Math.min(open, close) || low > high) {
        throw new Error(`Kronos forecast[${index}] OHLC关系无效`)
      }
    })
    return bars
  }

  if (!Array.isArray(value)) throw new Error('Kronos forecast 响应格式无效')
  return value.map((item, index) => {
    const row = record(item, `forecast[${index}]`)
    const keys = Object.keys(row)
    if (keys.length !== 2 || keys.some(key => key !== 'time' && key !== 'close')) {
      throw new Error(`Kronos forecast[${index}] close_only 只允许 time/close`)
    }
    return {
      time: text(row.time, `forecast[${index}].time`),
      close: positiveNumber(row.close, `forecast[${index}].close`),
    }
  })
}

function parseResult(value: unknown, request: KronosJobRequest): KronosResult {
  const input = record(value, '结果')
  if (input.model !== request.model || input.period !== request.period || input.symbol !== request.symbol) {
    throw new Error('Kronos 结果标的、模型或周期与请求不匹配')
  }
  const metrics = record(input.metrics, '指标')
  if (!Array.isArray(input.warnings) || input.warnings.some(item => typeof item !== 'string')) {
    throw new Error('Kronos warnings 响应格式无效')
  }
  let forecastMode: KronosForecastMode
  if (input.forecast_mode === undefined || input.forecast_mode === 'ohlc') {
    forecastMode = 'ohlc'
  } else if (input.forecast_mode === 'close_only') {
    forecastMode = 'close_only'
  } else {
    throw new Error('Kronos forecast_mode 响应格式无效')
  }
  const warnings = input.warnings as string[]
  if (forecastMode === 'close_only'
    && !warnings.some(warning => warning.includes(KRONOS_CLOSE_ONLY_WARNING))) {
    throw new Error('Kronos close_only 预测缺少收盘价路径说明')
  }
  const prevClose = parseOptionalPrevClose(input.prev_close)
  const prevCloseDate = parseOptionalDate(input.prev_close_date)
  return {
    symbol: text(input.symbol, '结果.symbol'),
    name: text(input.name, '结果.name'),
    model: input.model as KronosModel,
    period: input.period as KronosPeriod,
    forecast_mode: forecastMode,
    history: parseBars(input.history, 'history'),
    forecast: parseForecastBars(input.forecast, forecastMode),
    ...(prevClose !== undefined ? { prev_close: prevClose } : {}),
    ...(prevCloseDate !== undefined ? { prev_close_date: prevCloseDate } : {}),
    ...(input.chart_context !== undefined && input.chart_context !== null
      ? { chart_context: parseBars(input.chart_context, 'chart_context') }
      : {}),
    input_as_of: text(input.input_as_of, 'input_as_of'),
    requested_at: text(input.requested_at, '结果.requested_at'),
    price_basis: text(input.price_basis, 'price_basis'),
    warnings,
    metrics: {
      elapsed_seconds: finiteNumber(metrics.elapsed_seconds, 'elapsed_seconds'),
      cuda_peak_allocated_mb: finiteNumber(metrics.cuda_peak_allocated_mb, 'cuda_peak_allocated_mb'),
      cuda_peak_reserved_mb: finiteNumber(metrics.cuda_peak_reserved_mb, 'cuda_peak_reserved_mb'),
    },
  }
}

export function parseKronosJob(value: unknown): KronosJob {
  const input = record(value, '任务')
  if (typeof input.state !== 'string' || !JOB_STATES.has(input.state as KronosJobState)
    || (input.error !== null && typeof input.error !== 'string')) {
    throw new Error('Kronos 任务响应格式无效')
  }
  const request = parseKronosRequest(input.request)
  return {
    id: text(input.id, '任务.id'),
    state: input.state as KronosJobState,
    request,
    requested_at: text(input.requested_at, '任务.requested_at'),
    message: typeof input.message === 'string' ? input.message : '',
    error: input.error as string | null,
    result: input.result === null ? null : parseResult(input.result, request),
  }
}

export function isKronosJobActive(job: KronosJob | undefined): boolean {
  return job?.state === 'preparing' || job?.state === 'running'
}
