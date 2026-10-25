// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { StockDataTable } from './StockDataTable'
import type { ColumnConfig } from '@/lib/list-columns'

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
const host = document.createElement('div')
document.body.append(host)
let root = createRoot(host)
afterEach(async () => {
  await act(async () => root.unmount())
  root = createRoot(host)
})

it('supports keyboard sorting while preserving table and scroll semantics', async () => {
  const toggle = vi.fn()
  const columns = [{ id: 'close', label: '现价', visible: true, align: 'right', source: { type: 'builtin', key: 'close' } }] as ColumnConfig[]
  await act(async () => root.render(<StockDataTable columns={columns} rows={[{symbol:'600001.SH',close:1.234}]} renderCell={row => <td>{row.close}</td>} onSortToggle={toggle} sort={{key:'close',dir:'desc'}} />))
  const header = host.querySelector('th')!
  expect(host.querySelector('[role="region"]')?.getAttribute('tabindex')).toBe('0')
  expect(header.getAttribute('aria-sort')).toBe('descending')
  header.focus()
  await act(async () => { header.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',bubbles:true})) })
  await act(async () => { header.dispatchEvent(new KeyboardEvent('keydown', {key:' ',bubbles:true})) })
  expect(toggle.mock.calls).toEqual([['close'], ['close']])
})
