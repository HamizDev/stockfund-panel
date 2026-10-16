// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { usePageState } from './usePageState'

let root: Root
let host: HTMLDivElement
function Example() {
  const [value, setValue] = usePageState('abnormalTab', 'intraday', (v): v is string => v === 'auction' || v === 'intraday')
  return <button onClick={() => setValue('auction')}>{value}</button>
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  sessionStorage.clear()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { vi.restoreAllMocks(); await act(async () => root.unmount()); host.remove() })
it('restores a view after route unmount without retaining a mounted page', async () => {
  await act(async () => root.render(<Example />))
  await act(async () => host.querySelector('button')!.click())
  await act(async () => root.render(null))
  await act(async () => root.render(<Example />))
  expect(host.textContent).toBe('auction')
})
it.each(['{broken', 'null', '42', '"unknown"', '{}'])('ignores malformed or incompatible state %s', async raw => {
  sessionStorage.setItem('page-view:abnormal-tab', raw)
  await act(async () => root.render(<Example />))
  expect(host.textContent).toBe('intraday')
})
it('continues working when browser storage is unavailable', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  await act(async () => root.render(<Example />))
  await act(async () => host.querySelector('button')!.click())
  expect(host.textContent).toBe('auction')
})
