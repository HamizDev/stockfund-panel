import { afterEach, expect, it, vi } from 'vitest'
import type { FrontendExtension } from './types'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

async function freshRegistry() {
  vi.resetModules()
  return import('./registry')
}

function extension(id: string, path: `/${string}`): FrontendExtension {
  return {
    id,
    apiVersion: 1 as const,
    routes: [{ id: 'shared-route', path, component: () => null }],
  }
}

afterEach(() => vi.restoreAllMocks())

it('imports modules concurrently but registers them in source order', async () => {
  const registry = await freshRegistry()
  const a = deferred<void>()
  const z = deferred<void>()
  const started: string[] = []
  let active = 0
  let maxActive = 0

  const loader = (source: string, gate: Promise<void>, value: ReturnType<typeof extension>) => async () => {
    started.push(source)
    active += 1
    maxActive = Math.max(maxActive, active)
    await gate
    active -= 1
    return { default: value }
  }

  const loading = registry.loadFrontendExtensions({
    'z-extension.tsx': loader('z-extension.tsx', z.promise, extension('z-extension', '/z')),
    'a-extension.tsx': loader('a-extension.tsx', a.promise, extension('a-extension', '/a')),
  })

  expect(started).toEqual(['a-extension.tsx', 'z-extension.tsx'])
  expect(maxActive).toBe(2)
  z.resolve()
  await Promise.resolve()
  a.resolve()
  await loading

  registry.finalizeFrontendExtensions(new Set())
  expect(registry.getFrontendExtensionRoutes().map(route => route.path)).toEqual(['/a'])
  expect(registry.getFrontendExtensionLoadErrors()).toEqual([{
    source: 'z-extension.tsx',
    extensionId: 'z-extension',
    error: '扩展路由 ID 重复: shared-route',
  }])
})

it('isolates loader and registration failures without unhandled rejections', async () => {
  const registry = await freshRegistry()
  const slow = deferred<void>()
  const unhandled: unknown[] = []
  const onUnhandled = (reason: unknown) => unhandled.push(reason)
  process.on('unhandledRejection', onUnhandled)

  try {
    const loading = registry.loadFrontendExtensions({
      'a-reject.tsx': async () => { throw new Error('module load failed') },
      'b-missing.tsx': async () => ({ default: undefined as never }),
      'c-invalid.tsx': async () => ({ default: { id: 'bad-api', apiVersion: 2 } as never }),
      'd-slow-valid.tsx': async () => {
        await slow.promise
        return { default: extension('valid', '/valid') }
      },
    })

    slow.resolve()
    await loading
    await new Promise<void>(resolve => setTimeout(resolve, 0))
  } finally {
    process.off('unhandledRejection', onUnhandled)
  }

  expect(unhandled).toEqual([])
  expect(registry.getFrontendExtensionLoadErrors()).toEqual([
    { source: 'a-reject.tsx', error: 'module load failed' },
    { source: 'b-missing.tsx', error: '模块必须 default export FrontendExtension' },
    { source: 'c-invalid.tsx', extensionId: 'bad-api', error: '扩展 bad-api 需要前端契约 v2, 当前为 v1' },
  ])
  registry.finalizeFrontendExtensions(new Set())
  expect(registry.getFrontendExtensionRoutes().map(route => route.path)).toEqual(['/valid'])
})
