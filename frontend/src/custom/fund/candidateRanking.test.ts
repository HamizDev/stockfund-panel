import { describe, expect, it } from 'vitest'
import type { FundRankItem } from './client'
import { fundRankingBasis, mergeFundRankingCandidates, rankFundCandidates } from './candidateRanking'

const fund = (code: string, fund_type?: FundRankItem['fund_type']): FundRankItem => ({
  code,
  name: `基金${code}`,
  fund_type,
  share_class: 'A',
  nav: null,
  nav_date: null,
  purchase_fee_text: null,
  growth_1w: null,
  growth_1m: null,
  growth_3m: null,
  growth_6m: null,
  growth_1y: null,
  growth_2y: null,
  growth_3y: null,
})

describe('fund candidate ranking', () => {
  it('uses received order as the rank and numbers categories independently', () => {
    const ranked = rankFundCandidates([
      fund('000001', '股票型'),
      fund('000002', '股票型'),
      fund('000003', '债券型'),
    ], 'all')
    expect(ranked.map(({ rank, category }) => [rank, category])).toEqual([
      [1, '股票型'], [2, '股票型'], [1, '债券型'],
    ])
  })

  it('uses the selected fund type for old cached rows without category metadata', () => {
    const ranked = rankFundCandidates([fund('000001'), fund('000002')], '混合型')
    expect(ranked.map(({ rank, category }) => [rank, category])).toEqual([
      [1, '混合型'], [2, '混合型'],
    ])
  })

  it('preserves actual source ranks when AI candidates are interleaved across categories', () => {
    const ranked = rankFundCandidates([
      { ...fund('000001', '股票型'), ranking_rank: 7 },
      { ...fund('000002', '债券型'), ranking_rank: 3 },
      { ...fund('000003', '股票型'), ranking_rank: 12 },
    ], 'all')
    expect(ranked.map(({ rank }) => rank)).toEqual([7, 3, 12])
  })

  it('does not apply a duplicate code rank from another category', () => {
    const merged = mergeFundRankingCandidates([
      { ...fund('000001', '股票型'), ranking_rank: 2 },
    ], [
      { ...fund('000001', '混合型'), ranking_rank: 1 },
      { ...fund('000001', '股票型'), ranking_rank: 7 },
    ], 'all')

    expect(merged).toHaveLength(1)
    expect(merged[0]).toMatchObject({ code: '000001', fund_type: '股票型', ranking_rank: 7 })
  })

  it('does not imply a cross-category score or future return', () => {
    expect(fundRankingBasis('all', '近 1 年')).toContain('不对不同类型做综合排名')
    expect(fundRankingBasis('指数型', '近 3 月')).toContain('不是 AI 评分或未来收益预测')
  })
})
