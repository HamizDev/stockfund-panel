// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { SectorAnalysisTabs } from './SectorAnalysisTabs'

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('SectorAnalysisTabs', () => {
  it('switches concept and industry routes and exposes the active tab accessibly', async () => {
    await act(async () => root.render(
      <MemoryRouter initialEntries={['/concept-analysis']}>
        <SectorAnalysisTabs />
      </MemoryRouter>,
    ))

    const concept = host.querySelector<HTMLAnchorElement>('a[href="/concept-analysis"]')
    const industry = host.querySelector<HTMLAnchorElement>('a[href="/industry-analysis"]')
    expect(host.querySelector('nav[aria-label="板块研究分类"]')).not.toBeNull()
    expect(concept?.textContent).toBe('概念')
    expect(industry?.textContent).toBe('行业')
    expect(concept?.getAttribute('aria-current')).toBe('page')
    expect(industry?.hasAttribute('aria-current')).toBe(false)

    await act(async () => industry?.click())
    expect(concept?.hasAttribute('aria-current')).toBe(false)
    expect(industry?.getAttribute('aria-current')).toBe('page')

    await act(async () => concept?.click())
    expect(concept?.getAttribute('aria-current')).toBe('page')
    expect(industry?.hasAttribute('aria-current')).toBe(false)
  })
})