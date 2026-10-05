// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { EltdxMinuteTrial } from './EltdxMinuteTrial'

const mocks = vi.hoisted(() => ({ testDataSource: vi.fn() }))

vi.mock('@/lib/api', () => ({ api: { testDataSource: mocks.testDataSource } }))

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  mocks.testDataSource.mockReset()
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient.clear()
  host.remove()
})

async function render(available = true) {
  await act(async () => root.render(
    <QueryClientProvider client={queryClient}>
      <EltdxMinuteTrial available={available} />
    </QueryClientProvider>,
  ))
}

function button(label: string): HTMLButtonElement {
  const found = host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)
  if (!found) throw new Error(`button not found: ${label}`)
  return found
}

it('tests one symbol, disables both buttons while pending, and shows the observed Beijing end time', async () => {
  let resolveRequest!: (result: {
    provider: string
    dataset: string
    rows: number
    columns: string[]
    preview: Record<string, unknown>[]
    observed_end: string
    price_basis: string
    volume_unit: string
    amount_available: boolean
  }) => void
  mocks.testDataSource.mockReturnValue(new Promise(resolve => { resolveRequest = resolve }))

  await render()
  await act(async () => {
    button('试拉股票 600519.SH').click()
    await new Promise(resolve => setTimeout(resolve, 0))
  })

  expect(mocks.testDataSource).toHaveBeenCalledExactlyOnceWith(
    'eltdx_gateway', 'minute', ['600519.SH'],
  )
  expect(button('试拉股票 600519.SH').disabled).toBe(true)
  expect(button('试拉ETF 510300.SH').disabled).toBe(true)

  await act(async () => {
    resolveRequest({
      provider: 'eltdx_gateway',
      dataset: 'minute',
      rows: 7,
      columns: ['datetime', 'close'],
      preview: [],
      observed_end: '2026-09-30T07:00:00Z',
      price_basis: 'raw',
      volume_unit: '手',
      amount_available: false,
    })
    await new Promise(resolve => setTimeout(resolve, 0))
  })

  expect(host.textContent).toContain('试拉完成 · 7 根')
  expect(host.textContent).toContain('实际数据截止：2026-09-30 15:00:00 北京时间')
  expect(host.textContent).toContain('成交量单位：手')
  expect(host.textContent).toContain('成交额：不可用')
  expect(host.textContent).toContain('成交额单位尚未核实')
  expect(host.textContent).toContain('不复权原始价')
  expect(host.textContent).not.toContain('实时')
  expect(button('试拉ETF 510300.SH').disabled).toBe(false)
})

it('shows request errors as failures instead of reporting a zero-row success', async () => {
  mocks.testDataSource.mockRejectedValue(new Error('本机 ELTDX 网关连接失败'))
  await render()

  await act(async () => {
    button('试拉ETF 510300.SH').click()
    await new Promise(resolve => setTimeout(resolve, 0))
  })

  expect(mocks.testDataSource).toHaveBeenCalledExactlyOnceWith(
    'eltdx_gateway', 'minute', ['510300.SH'],
  )
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('本机 ELTDX 网关连接失败')
  expect(host.textContent).not.toContain('0 根')
  expect(host.querySelector('[role="status"]')).toBeNull()
})

it('disables both trials and sends no request when the plugin is unavailable', async () => {
  await render(false)

  expect(button('试拉股票 600519.SH').disabled).toBe(true)
  expect(button('试拉ETF 510300.SH').disabled).toBe(true)
  expect(host.textContent).toContain('插件依赖未就绪')
  expect(mocks.testDataSource).not.toHaveBeenCalled()
})

it('shows empty market responses as unavailable coverage rather than positive trial evidence', async () => {
  mocks.testDataSource.mockResolvedValue({
    provider: 'eltdx_gateway', dataset: 'minute', rows: 0, columns: [], preview: [],
    observed_end: null, amount_available: false,
  })
  await render()
  await act(async () => {
    button('试拉股票 600519.SH').click()
    await new Promise(resolve => setTimeout(resolve, 0))
  })
  expect(host.querySelector('[role="status"]')?.textContent).toContain('未返回分钟数据')
  expect(host.textContent).not.toContain('试拉完成')
  expect(host.textContent).not.toContain('实际数据截止')
})
