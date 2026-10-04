import { describe, expect, it } from 'vitest'
import { backtestResearchPrompt, paperResearchPrompt, researchLink, strategyResearchPrompt } from './researchContext'
import type { PaperCompareRow, StrategyBacktestResult, StrategyDetail } from '@/lib/api'

describe('bounded research context', () => {
  it('encodes IDs without allowing another route or query parameter', () => {
    expect(researchLink('paper', 'rule &next=/secret', 'etf')).toBe('/paper?strategy=rule+%26next%3D%2Fsecret&asset_type=etf')
  })
  it('uses the successful result identity and excludes arbitrary config/private fields', () => {
    const result = { run_id: 'previous-run', strategy_info: { id: 'saved_strategy', name: '历史策略' },
      config: { start: '2026-01-01', asset_type: 'etf', api_key: 'do-not-send' },
      stats: { total_return: 0.08, win_rate: 0.5, n_trades: 10, secrets: 'private' },
      equity_curve: [{ date: '2026-01-02', value: 100 }], trades: [] } as unknown as StrategyBacktestResult
    const prompt = backtestResearchPrompt(result)
    expect(prompt).toContain('previous-run')
    expect(prompt).toContain('"total_return":0.08')
    expect(prompt).not.toContain('do-not-send')
    expect(prompt).not.toContain('private')
  })
  it('keeps unknown settled valuation and no-sample win rate unknown', () => {
    const row = { strategy_id: 's1', pnl_pct: 99, total: 9999, rounds: 0, win_rate: 0, settled_pnl_pct: null } as PaperCompareRow
    const prompt = paperResearchPrompt([row])
    expect(prompt).toContain('"settled_pnl_pct":null')
    expect(prompt).toContain('"win_rate_pct":null')
    expect(prompt).not.toContain('9999')
  })
  it('keeps a typical 12-account context below the assistant 4000-character cap', () => {
    const rows = Array.from({ length: 12 }, (_, index) => ({ strategy_id: `strategy_${index}`, asset_type: 'stock', rounds: 12, wins: 6, win_rate: 50, nav_date: '2026-09-30', settled_pnl_pct: 2.1, holdings_count: 3 } as PaperCompareRow))
    expect(paperResearchPrompt(rows).length).toBeLessThan(4000)
  })
  it('bounds descriptions and uses only strategy metadata', () => {
    const detail = { id: 's1', name: '策略', description: 'x'.repeat(10000), entry_signals: [], exit_signals: [], api_key: 'not-allowed' } as unknown as StrategyDetail
    const prompt = strategyResearchPrompt(detail, 'etf')
    expect(prompt.length).toBeLessThan(4000)
    expect(prompt).not.toContain('not-allowed')
  })
})
