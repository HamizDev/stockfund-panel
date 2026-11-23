import type { KronosJob } from './types'

/** Offer an explicit new request; never silently change a requested analysis window. */
export function availableDailyWindow(job: KronosJob | null | undefined): number | null {
  if (job?.state !== 'failed' || job.request.period !== 'daily') return null
  if (!job.error?.startsWith('已自动在线补取并校验；')) return null
  const match = /只有(\d+)根可用K线，所选窗口需要(\d+)根/.exec(job.error ?? '')
  if (!match) return null
  const available = Number(match[1])
  const requested = Number(match[2])
  if (!Number.isSafeInteger(available) || requested !== job.request.lookback || available >= requested) return null
  return [512, 400, 240, 120, 60].find(count => count <= available && count < requested) ?? null
}
