import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'

const source = readFileSync(new URL('./Dashboard.tsx', import.meta.url), 'utf8')

it('uses two KPI columns on mobile, three from sm, and six on large screens', () => {
  const kpiGridClass = source.match(/<div className="([^"]+)">\s*<KpiCell label="个股涨 \/ 平 \/ 跌"/)?.[1]

  expect(kpiGridClass).toBe('mb-1.5 grid grid-cols-2 gap-1 sm:grid-cols-3 lg:grid-cols-6')
})
