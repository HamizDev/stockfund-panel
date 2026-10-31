import type { KronosModel, KronosPeriod } from './types'

export const KRONOS_LOOKBACKS = [60, 120, 240, 400, 512] as const
export const KRONOS_HORIZONS = [5, 10, 20] as const
export const KRONOS_SAMPLE_COUNTS = [1, 3, 5] as const

export type KronosAssetType = 'stock' | 'etf'

export interface KronosSelection {
  symbol: string
  name: string
  assetType?: KronosAssetType
}

export interface KronosViewState {
  model: KronosModel
  period: KronosPeriod
  lookback: number
  horizon: number
  samples: number
  selection: KronosSelection | null
  jobId: string | null
}

export const KRONOS_DEFAULT_STATE: KronosViewState = {
  model: 'base',
  period: 'daily',
  lookback: 120,
  horizon: 10,
  samples: 3,
  selection: null,
  jobId: null,
}

const SESSION_KEY = 'kronos-page-state-v1'
const JOB_ID_PATTERN = /^[0-9a-f]{32}$/

export function normalizeKronosLookback(period: KronosPeriod, lookback: number): number {
  return period === '1m' && lookback > 240 ? 240 : lookback
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function validAssetType(value: unknown): value is KronosAssetType {
  return value === 'stock' || value === 'etf'
}

function readSavedState(): KronosViewState {
  if (typeof window === 'undefined') return KRONOS_DEFAULT_STATE
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY)
    if (!raw) return KRONOS_DEFAULT_STATE
    const value: unknown = JSON.parse(raw)
    if (!isRecord(value)) return KRONOS_DEFAULT_STATE
    const selection = isRecord(value.selection)
      && typeof value.selection.symbol === 'string' && value.selection.symbol.trim()
      && typeof value.selection.name === 'string'
      ? {
          symbol: value.selection.symbol,
          name: value.selection.name,
          ...(validAssetType(value.selection.assetType) ? { assetType: value.selection.assetType } : {}),
        }
      : null
    const period: KronosPeriod = value.period === '1m' ? '1m' : 'daily'
    const lookback = KRONOS_LOOKBACKS.includes(value.lookback as typeof KRONOS_LOOKBACKS[number])
      ? value.lookback as number
      : 120
    return {
      model: 'base',
      period,
      lookback: normalizeKronosLookback(period, lookback),
      horizon: KRONOS_HORIZONS.includes(value.horizon as typeof KRONOS_HORIZONS[number]) ? value.horizon as number : 10,
      samples: KRONOS_SAMPLE_COUNTS.includes(value.samples as typeof KRONOS_SAMPLE_COUNTS[number]) ? value.samples as number : 3,
      selection,
      jobId: typeof value.jobId === 'string' && value.jobId.trim() ? value.jobId : null,
    }
  } catch {
    return KRONOS_DEFAULT_STATE
  }
}

/** Restore form/job state, with an explicit URL target taking precedence over the last selection. */
export function readKronosViewState(search = ''): KronosViewState {
  const saved = readSavedState()
  const params = new URLSearchParams(search)
  const symbol = params.get('symbol')?.trim()
  if (!symbol) return saved

  const requestedJobId = params.get('job')
  const jobId = requestedJobId && JOB_ID_PATTERN.test(requestedJobId) ? requestedJobId : saved.jobId
  const sameTarget = saved.selection?.symbol === symbol
  const queryAssetType = params.get('assetType')
  const period = params.get('period') === '1m' ? '1m' : params.get('period') === 'daily' ? 'daily' : saved.period
  return {
    ...saved,
    jobId,
    period,
    lookback: normalizeKronosLookback(period, saved.lookback),
    selection: {
      symbol,
      name: params.get('name')?.trim() || (sameTarget ? saved.selection?.name : undefined) || symbol,
      ...(validAssetType(queryAssetType)
        ? { assetType: queryAssetType }
        : sameTarget && saved.selection?.assetType
          ? { assetType: saved.selection.assetType }
          : {}),
    },
  }
}

export function writeKronosViewState(value: KronosViewState): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(value))
  } catch {
    // Browser storage can be disabled; the page still works for the current visit.
  }
}

export function kronosSearchParams(selection: KronosSelection, period: KronosPeriod): URLSearchParams {
  const params = new URLSearchParams({ symbol: selection.symbol, name: selection.name, period })
  if (selection.assetType) params.set('assetType', selection.assetType)
  return params
}
