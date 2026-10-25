// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GroupedNavigation } from './GroupedNavigation'
import type { NavigationGroupId } from '@/lib/navigationGroups'

const items = [
  { to: '/', label: '看板' },
  { to: '/markets/hk', label: '港股研究' },
  { to: '/watchlist', label: '自选' },
  { to: '/crypto-demo', label: '演示账户' },
  { to: '/research-tools', label: '研究工具' },
  { to: '/extensions/custom', label: '扩展页面' },
]

function Harness({
  pathname,
  initialRailMode = false,
  onExpandRail = () => {},
}: {
  pathname: string
  initialRailMode?: boolean
  onExpandRail?: () => void
}) {
  const [openGroup, setOpenGroup] = useState<NavigationGroupId | null>(null)
  const [railMode, setRailMode] = useState(initialRailMode)
  return <GroupedNavigation
    items={items}
    pathname={pathname}
    openGroup={openGroup}
    onGroupChange={setOpenGroup}
    railMode={railMode}
    onExpandRail={() => {
      onExpandRail()
      setRailMode(false)
    }}
    renderItem={item => <a href={item.to}>{item.label}</a>}
  />
}

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

describe('GroupedNavigation', () => {
  it('opens and closes the selected group in expanded mode', async () => {
    await act(async () => root.render(<Harness pathname="/market" />))
    const overview = host.querySelector<HTMLButtonElement>('button[aria-label="总览行情"]')
    expect(overview?.getAttribute('aria-expanded')).toBe('false')

    await act(async () => overview?.click())
    expect(overview?.getAttribute('aria-expanded')).toBe('true')
    expect(host.querySelector<HTMLDivElement>('#navigation-overview')?.hidden).toBe(false)
    expect(host.querySelector('a[href="/"]')?.textContent).toBe('看板')

    await act(async () => overview?.click())
    expect(overview?.getAttribute('aria-expanded')).toBe('false')
    expect(host.querySelector<HTMLDivElement>('#navigation-overview')?.hidden).toBe(true)
    expect(host.querySelector('a[href="/"]')).toBeNull()
  })

  it('marks crypto-demo as holdings and expands the selected group from rail mode', async () => {
    const onExpandRail = vi.fn()
    await act(async () => root.render(
      <Harness pathname="/crypto-demo" initialRailMode onExpandRail={onExpandRail} />,
    ))

    const holdings = host.querySelector<HTMLButtonElement>('button[aria-label="资产与模拟"]')
    expect(holdings?.className).toContain('text-accent')
    expect(holdings?.getAttribute('aria-expanded')).toBe('false')
    expect(holdings?.getAttribute('title')).toBe('资产与模拟')
    expect(host.querySelector('#navigation-holdings')).toBeNull()

    await act(async () => holdings?.click())
    expect(onExpandRail).toHaveBeenCalledTimes(1)
    expect(holdings?.getAttribute('aria-expanded')).toBe('true')
    expect(host.querySelector('a[href="/crypto-demo"]')?.textContent).toBe('演示账户')
  })
})