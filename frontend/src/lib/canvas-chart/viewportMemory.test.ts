// @vitest-environment jsdom
import { beforeEach, expect, it } from 'vitest'
import { storage } from '@/lib/storage'
import { sessionViewportMemory } from './viewportMemory'

beforeEach(() => sessionStorage.clear())
it('ignores corrupt view records and bounds tab-local history', () => {
  storage.chartViewports.set({ bad: { visibleCount: -1 }, invalid: null })
  expect(sessionViewportMemory.read('bad')).toBeNull()
  expect(sessionViewportMemory.read('invalid')).toBeNull()
  for (let i = 0; i < 50; i++) sessionViewportMemory.write(`board:${i}`, { endTime: i, visibleCount: 20, atLatest: false })
  expect(Object.keys(storage.chartViewports.get({}) as object)).toHaveLength(32)
  expect(sessionViewportMemory.read('board:0')).toBeNull()
  expect(sessionViewportMemory.read('board:49')?.endTime).toBe(49)
})
