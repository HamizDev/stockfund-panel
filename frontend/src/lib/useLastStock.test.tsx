// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { MAX_RECENT_STOCKS, useLastStock } from './useLastStock'

let host: HTMLDivElement, root: Root, controls: ReturnType<typeof useLastStock>
function Probe({ scope }: { scope: string }) {
  controls = useLastStock(scope)
  return <output>{JSON.stringify({ last: controls.last, recent: controls.recent })}</output>
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  localStorage.clear()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.restoreAllMocks()
  localStorage.clear()
})
async function render(scope = 'stock-analysis') {
  await act(async () => root.render(<Probe scope={scope} />))
}

it('restores the old single-stock record and bounds, deduplicates and promotes recent selections', async () => {
  localStorage.setItem('last_stock:stock-analysis', JSON.stringify({ symbol: '510300.SH', name: '沪深300ETF' }))
  await render()
  expect(controls.recent).toEqual([{ symbol: '510300.SH', name: '沪深300ETF' }])
  await act(async () => {
    for (let i = 0; i < 9; i++) controls.remember(`00000${i}.SZ`, `股票${i}`)
    controls.remember('000005.SZ', '新名称')
    controls.remember('000005.SZ', '000005.SZ')
  })
  expect(controls.recent).toHaveLength(MAX_RECENT_STOCKS)
  expect(controls.recent[0]).toEqual({ symbol: '000005.SZ', name: '新名称' })
  expect(new Set(controls.recent.map(item => item.symbol)).size).toBe(MAX_RECENT_STOCKS)
  expect(controls.last).toEqual(controls.recent[0])
  const saved = JSON.parse(localStorage.getItem('recent_stocks:stock-analysis')!)
  expect(saved).toEqual(controls.recent)
  expect(saved.every((item: object) => Object.keys(item).sort().join(',') === 'name,symbol')).toBe(true)
})

it('isolates markets on a scope change and preserves the current selection when recent records are cleared', async () => {
  await render('global-markets-hk')
  await act(async () => controls.remember('00700.HK', '腾讯控股'))
  await render('global-markets-us')
  expect(controls.recent).toEqual([])
  expect(controls.last).toBeNull()
  await act(async () => controls.remember('AAPL', '苹果'))
  await render('global-markets-hk')
  expect(controls.recent).toEqual([{ symbol: '00700.HK', name: '腾讯控股' }])
  await act(async () => controls.clearRecent())
  expect(controls.last?.symbol).toBe('00700.HK')
  expect(controls.recent).toEqual([])
  await act(async () => root.render(null))
  await render('global-markets-hk')
  expect(controls.recent).toEqual([])
  await act(async () => controls.clear())
  expect(localStorage.getItem('last_stock:global-markets-hk')).toBeNull()
  expect(controls.last).toBeNull()
  await render('global-markets-us')
  expect(controls.recent[0].symbol).toBe('AAPL')
})

it('ignores corrupt storage and strips price snapshots or oversized metadata from restored records', async () => {
  localStorage.setItem('last_stock:stock-analysis', 'bad json')
  localStorage.setItem('recent_stocks:stock-analysis', JSON.stringify([
    null, { symbol: 'bad symbol', name: 'bad' }, { symbol: 1, name: 'bad' },
    { symbol: '510300.SH', name: '沪深300ETF', price: 123, bars: [1, 2] },
    { symbol: '510300.SH', name: 'duplicate' },
    { symbol: '000001.SH', name: 'x'.repeat(1000) },
  ]))
  await render()
  expect(controls.last).toBeNull()
  expect(controls.recent).toHaveLength(2)
  expect(controls.recent[0]).toEqual({ symbol: '510300.SH', name: '沪深300ETF' })
  expect(controls.recent[1].name).toHaveLength(120)
})

it('keeps selection usable when browser storage is unavailable', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('blocked') })
  await render()
  await act(async () => controls.remember('510300.SH', '沪深300ETF'))
  expect(controls.recent[0].name).toBe('沪深300ETF')
  await act(async () => controls.clear())
  expect(controls.last).toBeNull()
})
