// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { focusManager, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { QK } from './queryKeys'
import { notifyPipelineJobStarted, useIndexQuotes, usePipelineJobs } from './useSharedQueries'

const fixtures = vi.hoisted(() => ({
  indexCalls: [] as Array<string[] | undefined>,
  pipelineCalls: [] as number[],
  pipelineActiveId: null as string | null,
}))

vi.mock('./api', () => ({
  api: {
    indexQuotes: async (symbols?: string[]) => {
      fixtures.indexCalls.push(symbols)
      return { rows: [], count: 5 }
    },
    pipelineJobs: async (limit = 20) => {
      fixtures.pipelineCalls.push(limit)
      return {
        active_id: fixtures.pipelineActiveId,
        jobs: Array.from({ length: limit }, (_, index) => ({ id: `job-${index + 1}` })),
      }
    },
  },
}))

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixtures.indexCalls.length = 0
  fixtures.pipelineCalls.length = 0
  fixtures.pipelineActiveId = null
  focusManager.setFocused(true)
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: {
    retry: false,
    gcTime: Infinity,
    refetchOnWindowFocus: false,
    refetchIntervalInBackground: true,
  } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
  focusManager.setFocused(true)
  vi.useRealTimers()
})

async function settle() {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

async function settleWithFakeTimers() {
  await act(async () => {
    for (let i = 0; i < 5; i++) {
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(0)
    }
  })
}

function IndexProbe({ id }: { id: string }) {
  const query = useIndexQuotes()
  return <output data-testid={`index-${id}`}>{query.data?.count ?? 'loading'}</output>
}

function PipelineProbe({ id, poll = false }: { id: string; poll?: boolean }) {
  const query = usePipelineJobs({ poll })
  return <output data-testid={`pipeline-${id}`}>
    {query.data ? `${query.data.jobs.length}:${query.data.active_id ?? 'idle'}` : 'loading'}
  </output>
}

it('shares one full index quote request between the sidebar and index page consumers', async () => {
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <><IndexProbe id="sidebar" /><IndexProbe id="page" /></>
    </QueryClientProvider>,
  ))
  await settle()

  expect(fixtures.indexCalls).toEqual([undefined])
  expect(host.querySelector('[data-testid="index-sidebar"]')?.textContent).toBe('5')
  expect(host.querySelector('[data-testid="index-page"]')?.textContent).toBe('5')
})

it('uses one Layout poll owner to discover external tasks and pauses hidden polling', async () => {
  vi.useFakeTimers()
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <><PipelineProbe id="layout" poll /><PipelineProbe id="data" /></>
    </QueryClientProvider>,
  ))
  await settleWithFakeTimers()

  expect(fixtures.pipelineCalls).toEqual([15])
  expect(host.querySelector('[data-testid="pipeline-layout"]')?.textContent).toBe('15:idle')
  expect(host.querySelector('[data-testid="pipeline-data"]')?.textContent).toBe('15:idle')

  const sharedQuery = client.getQueryCache().find({ queryKey: QK.pipelineJobs })
  const observers = (sharedQuery as any).observers as Array<any>
  expect(observers.map(observer => observer.options.refetchOnWindowFocus)).toEqual([true, true])
  expect(observers.map(observer => observer.options.refetchIntervalInBackground)).toEqual([false, false])
  const intervals = () => observers.map(observer => {
    const interval = observer.options.refetchInterval
    return typeof interval === 'function' ? interval(sharedQuery!) : interval
  })
  expect(intervals().sort((a: number | false, b: number | false) => Number(a) - Number(b))).toEqual([false, 60_000])

  fixtures.pipelineActiveId = 'job-from-another-tab'
  await act(async () => { await vi.advanceTimersByTimeAsync(59_999) })
  expect(fixtures.pipelineCalls).toEqual([15])
  await act(async () => { await vi.advanceTimersByTimeAsync(1) })
  await settleWithFakeTimers()
  expect(fixtures.pipelineCalls).toEqual([15, 15])
  expect(client.getQueryData(QK.pipelineJobs)).toMatchObject({ active_id: 'job-from-another-tab' })

  expect(intervals().sort((a: number | false, b: number | false) => Number(a) - Number(b))).toEqual([false, 2_000])

  focusManager.setFocused(false)
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })
  expect(fixtures.pipelineCalls).toEqual([15, 15])

  focusManager.setFocused(true)
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })
  await settleWithFakeTimers()
  expect(fixtures.pipelineCalls).toEqual([15, 15, 15])
})

it('notifies the shared job query when a task starts', async () => {
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <><PipelineProbe id="layout" /><PipelineProbe id="data" /></>
    </QueryClientProvider>,
  ))
  await settle()

  fixtures.pipelineActiveId = 'job-started'
  notifyPipelineJobStarted(client, 'job-started')
  expect(client.getQueryData(QK.pipelineJobs)).toMatchObject({ active_id: 'job-started' })
  await settle()

  expect(fixtures.pipelineCalls).toEqual([15, 15])
  expect(client.getQueryData(QK.pipelineJobs)).toMatchObject({ active_id: 'job-started' })
})
