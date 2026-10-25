// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { SectorRotationFreshness } from './SectorRotationCard'

vi.mock('@/components/Layout', () => ({ CORE_INDEXES: [{ symbol: '000001.SH', name: '上证指数' }] }))

describe('sector rotation minute freshness', () => {
  it('distinguishes an old minute replay from newer market data without changing its date', () => {
    const html = renderToStaticMarkup(<SectorRotationFreshness date="2026-10-12" asOf="13:05" referenceDate="2026-10-23" />)
    expect(html).toContain('分钟数据滞后')
    expect(html).toContain('2026-10-12 13:05')
    expect(html).toContain('2026-10-23')
    expect(html).toContain('当前展示历史回放')
    expect(html).toContain('重新读取不会补齐缺失分钟')
  })

  it.each([
    ['2026-10-23', '2026-10-23'],
    ['2026-10-23', '2026-10-22'],
    ['2026-10-23', undefined],
    [undefined, '2026-10-23'],
    ['最新', '2026-10-23'],
  ])('does not infer a gap for %s relative to %s', (date, referenceDate) => {
    expect(renderToStaticMarkup(<SectorRotationFreshness date={date} referenceDate={referenceDate} />)).toBe('')
  })
})
