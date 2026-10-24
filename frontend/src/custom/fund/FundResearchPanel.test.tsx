// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { FundResearch } from './client'
import { FundResearchPanel } from './FundResearchPanel'

const reportUrl = 'https://fund.eastmoney.com/report/012345-2026-09'

function makeResearch(): FundResearch {
  return {
    thscode: '012345.OF',
    source: 'eastmoney',
    retrieved_at_ms: Date.now(),
    horizon: '1y',
    nav_date: '2026-10-19',
    fees: {
      status: 'ok',
      management_pct: 0.5,
      custody_pct: 0.1,
      sales_service_pct: null,
      subscription_rules: [],
      redemption_rules: [],
      source_url: 'https://fund.eastmoney.com/fees/012345',
      as_of: null,
      note: '',
    },
    risk: {
      status: 'ok',
      basis: 'source_return_series',
      max_drawdown_pct: 12.3,
      start_date: '2025-10-20',
      end_date: '2026-10-19',
      observations: 52,
      note: '',
      source_url: 'https://fund.eastmoney.com/risk/012345',
    },
    holdings: {
      status: 'ok',
      report_date: '2026-06-30',
      publication_date: null,
      items: [],
      coverage_weight_pct: 63.5,
      report_note: '',
      source_url: 'https://fund.eastmoney.com/holdings/012345',
      related_reports: [{
        title: '2026年中期报告',
        publication_date: '2026-08-25',
        report_date: '2026-06-30',
        source_url: reportUrl,
      }],
    },
    missing_fields: ['nav_date'],
    warnings: [],
  }
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

describe('FundResearchPanel source presentation', () => {
  it('hides provider links while keeping report actions, data dates, and sampled-risk caveat', async () => {
    await act(async () => root.render(<FundResearchPanel thscode="012345.OF" research={makeResearch()} enabled={false} />))

    const text = host.textContent ?? ''
    expect(text).not.toContain('东方财富')
    expect(text).not.toContain('查看来源')
    expect(text).toContain('累计收益图表按采样点估算，并非逐日精确最大回撤。')
    expect(text).toContain('未取到：净值日期')
    expect(text).toContain('报告期：2026-06-30')
    expect(text).toContain('公告 2026-08-25 · 报告期 2026-06-30')

    const links = Array.from(host.querySelectorAll<HTMLAnchorElement>('a[href^="https://"]'))
    expect(links).toHaveLength(1)
    expect(links[0].href).toBe(reportUrl)
    expect(links[0].textContent).toContain('2026年中期报告')
  })

  it('keeps the unadjusted unit-nav risk basis and dividend caveat', async () => {
    const research = makeResearch()
    research.risk.basis = 'unit_nav'
    await act(async () => root.render(<FundResearchPanel thscode="012345.OF" research={research} enabled={false} />))

    const text = host.textContent ?? ''
    expect(text).toContain('单位净值（未复权）')
    expect(text).toContain('使用未复权单位净值；分红会影响该回撤结果。')
  })
})
