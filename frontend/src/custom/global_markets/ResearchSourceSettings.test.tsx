// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ResearchSourceSettings } from './ResearchSourceSettings'

type Provider = 'finnhub' | 'adanos'

const mocks = vi.hoisted(() => ({
  queryKey: ['global-research-sources'] as const,
  globalResearchSources: vi.fn(),
  saveGlobalResearchKey: vi.fn(),
  clearGlobalResearchKey: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ api: {
  globalResearchSources: mocks.globalResearchSources,
  saveGlobalResearchKey: mocks.saveGlobalResearchKey,
  clearGlobalResearchKey: mocks.clearGlobalResearchKey,
} }))
vi.mock('@/lib/queryKeys', () => ({ QK: { globalResearchSources: mocks.queryKey } }))

function sourceStatus(configured?: Provider) {
  return {
    sources: (['finnhub', 'adanos'] as const).map(provider => ({
      provider,
      configured: provider === configured,
      masked: provider === configured ? `${provider.slice(0, 2)}•••${provider.slice(-2)}` : '',
    })),
    storage: 'local_json' as const,
  }
}

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  mocks.globalResearchSources.mockReset().mockImplementation(async () => sourceStatus())
  mocks.saveGlobalResearchKey.mockReset().mockImplementation(async (provider: Provider) => sourceStatus(provider))
  mocks.clearGlobalResearchKey.mockReset().mockImplementation(async () => sourceStatus())
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient.clear()
  host.remove()
})

async function settle() {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)) })
}

async function renderSettings() {
  await act(async () => root.render(
    <QueryClientProvider client={queryClient}><ResearchSourceSettings /></QueryClientProvider>,
  ))
  await settle()
}

async function changeInput(provider: Provider, value: string) {
  let input!: HTMLInputElement
  await act(async () => {
    input = host.querySelector<HTMLInputElement>(`#${provider}-api-key`)!
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  return input
}

async function clickButton(label: string) {
  const button = [...host.querySelectorAll('button')].find(candidate => candidate.textContent?.includes(label))
  expect(button).toBeTruthy()
  await act(async () => button!.click())
  await settle()
}

it('validates and saves a key without retaining it in the input, query cache, or rendered UI', async () => {
  const key = 'test-finnhub-key-do-not-persist'
  await renderSettings()
  mocks.globalResearchSources.mockResolvedValue(sourceStatus('finnhub'))
  const input = await changeInput('finnhub', key)
  const form = input.closest('form')!

  await act(async () => {
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await new Promise(resolve => setTimeout(resolve, 50))
  })
  await settle()

  expect(mocks.saveGlobalResearchKey).toHaveBeenCalledWith('finnhub', key)
  expect(input.value).toBe('')
  expect(host.textContent).toContain('Key 已验证并保存。')
  expect(host.textContent).toContain('fi•••ub')
  expect(host.textContent).not.toContain(key)
  expect(JSON.stringify(queryClient.getQueryCache().getAll().map(query => query.state.data))).not.toContain(key)
})

it('clears the password input and hides provider error details when validation fails', async () => {
  const key = 'test-finnhub-key-failure'
  await renderSettings()
  mocks.saveGlobalResearchKey.mockRejectedValue(new Error(`request failed: ?token=${key}`))
  const input = await changeInput('finnhub', key)
  const form = input.closest('form')!

  await act(async () => {
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await new Promise(resolve => setTimeout(resolve, 50))
  })
  await settle()

  expect(input.value).toBe('')
  expect(host.textContent).toContain('验证或保存失败。')
  expect(host.textContent).not.toContain(key)
  expect(host.textContent).not.toContain('?token=')
})

it('requires inline confirmation before clearing and invalidates only relevant cached data', async () => {
  await renderSettings()
  mocks.globalResearchSources.mockResolvedValue(sourceStatus())
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
  const remove = vi.spyOn(queryClient, 'removeQueries')

  await act(async () => {
    await queryClient.setQueryData(mocks.queryKey, sourceStatus('adanos'))
  })
  await settle()

  await clickButton('移除本机 Key')
  expect(mocks.clearGlobalResearchKey).not.toHaveBeenCalled()
  expect(host.textContent).toContain('确认移除')
  await clickButton('确认移除')

  expect(mocks.clearGlobalResearchKey).toHaveBeenCalledWith('adanos')
  expect(host.textContent).toContain('本机保存的 Key 已移除。')
  expect(invalidate).toHaveBeenCalledWith({ queryKey: mocks.queryKey })
  expect(remove).toHaveBeenCalledWith({ queryKey: ['global-research'] })
})
