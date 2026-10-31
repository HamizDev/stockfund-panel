import { describe, expect, it } from 'vitest'
import {
  defaultKronosWorkbenchSession,
  KRONOS_WORKBENCH_SESSION_KEY,
  readKronosWorkbenchSession,
  updateKronosWorkbenchSession,
  type KronosSessionStorage,
} from './workbenchSession'

class MemoryStorage implements KronosSessionStorage {
  value: string | null = null

  getItem(key: string): string | null {
    expect(key).toBe(KRONOS_WORKBENCH_SESSION_KEY)
    return this.value
  }

  setItem(key: string, value: string): void {
    expect(key).toBe(KRONOS_WORKBENCH_SESSION_KEY)
    this.value = value
  }
}

describe('Kronos lightweight workbench session', () => {
  it('restores only tab, parameters and current-scope identifiers', () => {
    const storage = new MemoryStorage()
    storage.value = JSON.stringify({
      activeTab: 'comparison',
      models: ['base'],
      lookbacks: [120, 400],
      horizon: 20,
      samples: 5,
      scope: { symbol: '600000.SH', period: 'daily' },
      selectedBatchId: 'batch-1',
      selectedTrialId: 'trial-1',
      history: [1, 2, 3],
      result: { forecast: [1, 2, 3] },
    })

    expect(readKronosWorkbenchSession(storage)).toEqual({
      activeTab: 'comparison',
      models: ['base'],
      lookbacks: [120, 400],
      horizon: 20,
      samples: 5,
      scope: { symbol: '600000.SH', period: 'daily' },
      selectedBatchId: 'batch-1',
      selectedTrialId: 'trial-1',
    })
  })

  it('falls back to defaults for malformed JSON or invalid session values', () => {
    const storage = new MemoryStorage()
    storage.value = '{bad json'
    expect(readKronosWorkbenchSession(storage)).toEqual(defaultKronosWorkbenchSession())

    storage.value = JSON.stringify({
      ...defaultKronosWorkbenchSession(),
      activeTab: 'unknown',
      lookbacks: [60, 120, 240, 400],
    })
    expect(readKronosWorkbenchSession(storage)).toEqual(defaultKronosWorkbenchSession())
  })

  it('keeps the in-memory path usable when browser storage throws', () => {
    const blockedStorage: KronosSessionStorage = {
      getItem: () => { throw new Error('blocked') },
      setItem: () => { throw new Error('blocked') },
    }

    expect(readKronosWorkbenchSession(blockedStorage)).toEqual(defaultKronosWorkbenchSession())
    expect(() => updateKronosWorkbenchSession({ activeTab: 'summary' }, blockedStorage)).not.toThrow()
  })

  it('merges a small state update without writing result data', () => {
    const storage = new MemoryStorage()
    updateKronosWorkbenchSession({
      activeTab: 'comparison',
      scope: { symbol: '600000.SH', period: '1m' },
      selectedBatchId: 'batch-2',
      selectedTrialId: 'trial-3',
    }, storage)

    expect(readKronosWorkbenchSession(storage)).toMatchObject({
      activeTab: 'comparison',
      scope: { symbol: '600000.SH', period: '1m' },
      selectedBatchId: 'batch-2',
      selectedTrialId: 'trial-3',
    })
  })
})
