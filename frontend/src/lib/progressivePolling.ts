const FIRST_INTERVAL_MS = 2_000
const EARLY_BACKOFF_MS = 15_000
const MEDIUM_BACKOFF_MS = 60_000
const LATE_BACKOFF_MS = 120_000

interface ProgressState {
  token: string
  progressedAt: number
}

// Query objects are unique per query key (including each market/mode), and WeakMap
// lets abandoned queries release their polling state without a global cleanup pass.
const progressByQuery = new WeakMap<object, ProgressState>()

function progressToken(progress: unknown): string {
  if (typeof progress === 'string') return progress
  if (typeof progress === 'number' || typeof progress === 'boolean' || progress == null) return String(progress)
  try { return JSON.stringify(progress) ?? String(progress) } catch { return String(progress) }
}

/** Poll quickly while loading advances, then back off by elapsed idle time. */
export function progressiveLoadingInterval(
  query: object,
  loading: boolean,
  progress: unknown,
  settledIntervalMs: number,
  now = Date.now(),
): number {
  if (!loading) {
    progressByQuery.delete(query)
    return settledIntervalMs
  }

  const token = progressToken(progress)
  const state = progressByQuery.get(query)
  if (!state || state.token !== token) {
    progressByQuery.set(query, { token, progressedAt: now })
    return FIRST_INTERVAL_MS
  }

  const idleMs = Math.max(0, now - state.progressedAt)
  if (idleMs < EARLY_BACKOFF_MS) return FIRST_INTERVAL_MS
  if (idleMs < MEDIUM_BACKOFF_MS) return 5_000
  if (idleMs < LATE_BACKOFF_MS) return 15_000
  return 30_000
}
