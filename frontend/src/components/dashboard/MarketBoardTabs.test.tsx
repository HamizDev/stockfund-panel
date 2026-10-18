// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { MarketBoardTabs } from './MarketBoardTabs'

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

it('links to all three dashboard regions and marks only the selected region current', async () => {
  const cases = [
    { market: 'cn', label: 'A股看板', href: '/?market=cn' },
    { market: 'hk', label: '港股看板', href: '/?market=hk' },
    { market: 'us', label: '美股看板', href: '/?market=us' },
  ] as const

  for (const active of cases) {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/']}>
          <MarketBoardTabs market={active.market} />
        </MemoryRouter>,
      )
    })

    const links = [...host.querySelectorAll<HTMLAnchorElement>('nav a')]
    expect(links.map(link => [link.textContent, link.getAttribute('href')])).toEqual(
      cases.map(({ label, href }) => [label, href]),
    )
    expect(links.filter(link => link.getAttribute('aria-current') === 'page')).toHaveLength(1)
    expect(links.find(link => link.textContent === active.label)?.getAttribute('aria-current')).toBe('page')
    expect(links.filter(link => link.textContent !== active.label).every(link => !link.hasAttribute('aria-current'))).toBe(true)
  }
})
