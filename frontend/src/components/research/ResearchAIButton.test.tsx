// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ResearchAIButton } from './ResearchAIButton'

const mocks = vi.hoisted(() => ({ status: vi.fn(), open: vi.fn(), send: vi.fn(), sending: false }))
vi.mock('@/custom/assistant/client', () => ({ fetchAssistantStatus: mocks.status }))
vi.mock('@/custom/assistant/store', () => ({ openAssistant: mocks.open, sendResearchMessage: mocks.send, useAssistantStore: () => ({ sending: mocks.sending }) }))
let host: HTMLDivElement
let root: Root
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.clearAllMocks()
  mocks.send.mockImplementation(() => { mocks.open(); return true })
  mocks.sending = false
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove() })
async function click() {
  await act(async () => { host.querySelector('button')!.click(); await Promise.resolve() })
}
it('does not start a model request merely by rendering', () => {
  act(() => root.render(<ResearchAIButton label="AI 解读" prompt="actual summary" />))
  expect(mocks.status).not.toHaveBeenCalled()
  expect(mocks.send).not.toHaveBeenCalled()
})
it('opens the existing assistant and sends the selected summary after an explicit click', async () => {
  mocks.status.mockResolvedValue({ configured: true, supports_tools: true })
  act(() => root.render(<ResearchAIButton label="AI 解读" prompt="actual summary" />))
  await click()
  expect(mocks.open).toHaveBeenCalledTimes(1)
  expect(mocks.send).toHaveBeenCalledWith('actual summary')
})
it('guides unconfigured users without consuming a model request', async () => {
  mocks.status.mockResolvedValue({ configured: false, supports_tools: false })
  act(() => root.render(<ResearchAIButton label="AI 解读" prompt="summary" />))
  await click()
  expect(mocks.send).not.toHaveBeenCalled()
  expect(host.textContent).toContain('请先在设置中配置')
})
it('reports status failure and prevents sending while another generation is active', async () => {
  mocks.status.mockRejectedValue(new Error('offline'))
  act(() => root.render(<ResearchAIButton label="AI 解读" prompt="summary" />))
  await click()
  expect(host.textContent).toContain('无法检查 AI 助手连接')
  expect(mocks.send).not.toHaveBeenCalled()
  mocks.sending = true
  act(() => root.render(<ResearchAIButton label="AI 解读" prompt="summary" />))
  expect(host.querySelector('button')!.disabled).toBe(true)
})
it('refuses oversized context without silently truncating it or calling the model', async () => {
  act(() => root.render(<ResearchAIButton label="AI 解读" prompt={'x'.repeat(4001)} />))
  await click()
  expect(mocks.status).not.toHaveBeenCalled()
  expect(mocks.send).not.toHaveBeenCalled()
  expect(host.textContent).toContain('摘要过长')
})
