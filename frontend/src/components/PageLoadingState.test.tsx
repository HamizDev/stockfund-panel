// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it } from 'vitest'
import { PageLoadingState, PageStartupError } from './PageLoadingState'

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
const host = document.createElement('div')
document.body.append(host)
let root = createRoot(host)
afterEach(async () => {
  await act(async () => root.unmount())
  root = createRoot(host)
})

it('announces page preparation without implying market data has arrived', async () => {
  await act(async () => root.render(<PageLoadingState title="正在打开自选" />))
  expect(host.querySelector('h1')?.textContent).toBe('正在打开自选')
  expect(host.querySelector('[aria-busy="true"]')).not.toBeNull()
  expect(host.querySelector('[role="status"]')?.textContent).toContain('自动显示')
  expect(host.querySelector('button')).toBeNull()
})

it('shows an actionable startup error instead of an empty root', async () => {
  await act(async () => root.render(<PageStartupError />))
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('刷新')
  expect(host.querySelector('button')?.textContent).toContain('刷新页面')
})
