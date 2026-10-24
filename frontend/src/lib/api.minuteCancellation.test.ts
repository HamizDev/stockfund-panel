// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { api } from './api'

const mocks = vi.hoisted(() => ({ toast: vi.fn() }))
vi.mock('@/components/Toast', () => ({ toast: mocks.toast }))

afterEach(() => {
  vi.unstubAllGlobals()
  mocks.toast.mockClear()
})

it.each(['stock', 'index'] as const)('cancels an abandoned %s minute request without retrying or reporting an error', async kind => {
  const controller = new AbortController()
  const fetchMock = vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal!.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')), { once: true })
  }))
  vi.stubGlobal('fetch', fetchMock)
  const pending = kind === 'stock'
    ? api.klineMinute('510300.SH', undefined, true, controller.signal)
    : api.indexMinute('000001.SH', undefined, controller.signal)
  const rejection = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  controller.abort()
  await rejection
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(fetchMock.mock.calls[0][1].signal?.aborted).toBe(true)
  expect(mocks.toast).not.toHaveBeenCalled()
})
