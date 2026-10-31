import type { KronosModel, KronosPeriod } from './types'

export type KronosWorkbenchTab = 'single' | 'comparison' | 'summary' | 'candidates'

export interface KronosWorkbenchScope {
  symbol: string
  period: KronosPeriod
}

export interface KronosWorkbenchSession {
  activeTab: KronosWorkbenchTab
  models: KronosModel[]
  lookbacks: number[]
  horizon: 5 | 10 | 20
  samples: 1 | 3 | 5
  scope: KronosWorkbenchScope | null
  selectedBatchId: string | null
  selectedTrialId: string | null
}

export interface KronosSessionStorage {
  getItem: (key: string) => string | null
  setItem: (key: string, value: string) => void
}

export const KRONOS_WORKBENCH_SESSION_KEY = 'kronos-workbench:v1'

const TABS = new Set<KronosWorkbenchTab>(['single', 'comparison', 'summary', 'candidates'])
const MODELS = new Set<KronosModel>(['base'])
const PERIODS = new Set<KronosPeriod>(['daily', '1m'])
const LOOKBACKS = new Set([60, 120, 240, 400, 512])
const HORIZONS = new Set([5, 10, 20])
const SAMPLES = new Set([1, 3, 5])

export function defaultKronosWorkbenchSession(): KronosWorkbenchSession {
  return {
    activeTab: 'single',
    models: ['base'],
    lookbacks: [60, 120, 240],
    horizon: 10,
    samples: 3,
    scope: null,
    selectedBatchId: null,
    selectedTrialId: null,
  }
}

function sessionStorageOrNull(storage?: KronosSessionStorage): KronosSessionStorage | null {
  if (storage) return storage
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function validId(value: unknown): value is string | null {
  return value === null || (typeof value === 'string' && value.length > 0 && value.length <= 128)
}

function parseSession(value: unknown): KronosWorkbenchSession | null {
  if (!isRecord(value)
    || typeof value.activeTab !== 'string' || !TABS.has(value.activeTab as KronosWorkbenchTab)
    || !Array.isArray(value.models) || value.models.length > 2
    || value.models.some(model => typeof model !== 'string' || !MODELS.has(model as KronosModel))
    || new Set(value.models).size !== value.models.length
    || !Array.isArray(value.lookbacks) || value.lookbacks.length > 3
    || value.lookbacks.some(window => typeof window !== 'number' || !LOOKBACKS.has(window))
    || new Set(value.lookbacks).size !== value.lookbacks.length
    || typeof value.horizon !== 'number' || !HORIZONS.has(value.horizon)
    || typeof value.samples !== 'number' || !SAMPLES.has(value.samples)
    || !validId(value.selectedBatchId) || !validId(value.selectedTrialId)) {
    return null
  }

  let scope: KronosWorkbenchScope | null = null
  if (value.scope !== null) {
    if (!isRecord(value.scope) || typeof value.scope.symbol !== 'string' || !value.scope.symbol.trim()
      || value.scope.symbol.length > 64 || typeof value.scope.period !== 'string'
      || !PERIODS.has(value.scope.period as KronosPeriod)) return null
    scope = { symbol: value.scope.symbol, period: value.scope.period as KronosPeriod }
  }
  if (value.selectedBatchId !== null && scope === null) return null
  if (value.selectedTrialId !== null && value.selectedBatchId === null) return null

  return {
    activeTab: value.activeTab as KronosWorkbenchTab,
    models: [...value.models] as KronosModel[],
    lookbacks: [...value.lookbacks] as number[],
    horizon: value.horizon as 5 | 10 | 20,
    samples: value.samples as 1 | 3 | 5,
    scope,
    selectedBatchId: value.selectedBatchId,
    selectedTrialId: value.selectedTrialId,
  }
}

export function readKronosWorkbenchSession(storage?: KronosSessionStorage): KronosWorkbenchSession {
  const target = sessionStorageOrNull(storage)
  if (!target) return defaultKronosWorkbenchSession()
  try {
    const raw = target.getItem(KRONOS_WORKBENCH_SESSION_KEY)
    if (raw === null) return defaultKronosWorkbenchSession()
    return parseSession(JSON.parse(raw)) ?? defaultKronosWorkbenchSession()
  } catch {
    return defaultKronosWorkbenchSession()
  }
}

export function updateKronosWorkbenchSession(
  patch: Partial<KronosWorkbenchSession>,
  storage?: KronosSessionStorage,
): void {
  const target = sessionStorageOrNull(storage)
  if (!target) return
  try {
    const current = readKronosWorkbenchSession(target)
    const next = parseSession({ ...current, ...patch })
    if (next) target.setItem(KRONOS_WORKBENCH_SESSION_KEY, JSON.stringify(next))
  } catch {
    // sessionStorage can be blocked or quota-limited; in-memory UI state remains usable.
  }
}
