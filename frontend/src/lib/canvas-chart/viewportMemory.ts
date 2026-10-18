import { storage } from '@/lib/storage'
import type { CanvasViewportMemory, StoredCanvasViewport } from './math'

function records(): Record<string, unknown> {
  const value = storage.chartViewports.get(null)
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

/** A tab-local, bounded history of chart views. No prices or account data. */
export const sessionViewportMemory: CanvasViewportMemory = {
  read(key) {
    const value = records()[key] as Partial<StoredCanvasViewport> | undefined
    if (!value || typeof value !== 'object' || typeof value.atLatest !== 'boolean'
      || !Number.isFinite(value.visibleCount) || value.visibleCount! < 1 || value.visibleCount! > 100_000
      || !(typeof value.endTime === 'number' && Number.isFinite(value.endTime)
        || typeof value.endTime === 'string' && value.endTime.length <= 32)) return null
    return value as StoredCanvasViewport
  },
  write(key, value) {
    const entries = Object.entries(records()).filter(([id]) => id !== key).slice(-31)
    storage.chartViewports.set(Object.fromEntries([...entries, [key, value]]))
  },
}
