// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider, QueryObserver } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { useInstrumentViewCache } from './useInstrumentViewCache'

let client: QueryClient, host: HTMLDivElement, root: Root
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  client = new QueryClient()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove() })
function Keep({ symbol, date }: { symbol: string; date: string | null }) {
  useInstrumentViewCache('analysis', symbol, date)
  return null
}
async function render(symbol: string, date: string | null = null) {
  await act(async () => {
    root.render(<QueryClientProvider client={client}><Keep symbol={symbol} date={date} /></QueryClientProvider>)
    await Promise.resolve()
  })
}
function seed(key: string[], symbol: string, date?: string, scope = 'analysis') {
  client.setQueryDefaults(key, { meta: { instrumentView: scope, symbol, minuteDate: date } })
  client.setQueryData(key, { rows: [1, 2, 3] })
}

it('releases only inactive old instruments and old historical minutes in this view', async () => {
  seed(['old'], 'A'); seed(['latest'], 'B', ''); seed(['current'], 'B', '2026-10-16')
  seed(['history'], 'B', '2026-10-15'); seed(['other'], 'A', undefined, 'indices')
  await render('B', '2026-10-16')
  expect(client.getQueryData(['old'])).toBeUndefined()
  expect(client.getQueryData(['history'])).toBeUndefined()
  for (const key of ['latest', 'current', 'other']) expect(client.getQueryData([key])).toBeTruthy()
  await act(async () => root.render(<div />))
  expect(client.getQueryData(['current'])).toBeTruthy()
})

it('protects even a disabled query with another mounted observer', async () => {
  seed(['observed'], 'A')
  const observer = new QueryObserver(client, { queryKey: ['observed'], enabled: false,
    meta: { instrumentView: 'analysis', symbol: 'A' } })
  const unsubscribe = observer.subscribe(() => {})
  await render('B')
  expect(client.getQueryData(['observed'])).toBeTruthy()
  unsubscribe()
})

it('cancels an inactive old request and prevents its late result from restoring the cache', async () => {
  let signal: AbortSignal | undefined, finish!: (data: object) => void
  const pending = client.fetchQuery({ queryKey: ['pending'], meta: { instrumentView: 'analysis', symbol: 'A' },
    queryFn: context => { signal = context.signal; return new Promise(resolve => { finish = resolve }) } }).catch(() => {})
  await render('B')
  expect(signal?.aborted).toBe(true)
  finish({ rows: [1] }); await pending
  expect(client.getQueryData(['pending'])).toBeUndefined()
})
