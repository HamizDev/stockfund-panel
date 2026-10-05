import type { AiFundType, FundRankItem } from './client'

export interface RankedFundCandidate {
  item: FundRankItem
  rank: number
  category: string | null
  rankSource: 'this-run' | 'filtered-ranking'
}

/** Merge readonly ranking rows without replacing an AI candidate with another fund category. */
export function mergeFundRankingCandidates(
  candidates: readonly FundRankItem[],
  rankings: readonly FundRankItem[],
  resultFundType: AiFundType,
): FundRankItem[] {
  const merged = new Map(candidates.map((item) => [item.code, item]))
  for (const item of rankings) {
    const current = merged.get(item.code)
    if (!current) {
      merged.set(item.code, item)
      continue
    }
    const currentCategory = current.fund_type ?? (resultFundType === 'all' ? null : resultFundType)
    if (!currentCategory || currentCategory !== item.fund_type) continue
    merged.set(item.code, { ...item, ...current, fund_type: currentCategory, ranking_rank: item.ranking_rank })
  }
  return [...merged.values()]
}

/** Preserve upstream order and number each fund category independently. */
export function rankFundCandidates(items: readonly FundRankItem[], resultFundType: AiFundType): RankedFundCandidate[] {
  const counts = new Map<string, number>()
  return items.map((item) => {
    const category = item.fund_type ?? (resultFundType === 'all' ? null : resultFundType)
    const bucket = category ?? 'legacy-unclassified'
    const previous = counts.get(bucket) ?? 0
    const hasFilteredRankingPosition = Number.isInteger(item.ranking_rank) && (item.ranking_rank ?? 0) > 0
    const rank = hasFilteredRankingPosition ? item.ranking_rank as number : previous + 1
    counts.set(bucket, Math.max(previous, rank))
    return { item, rank, category, rankSource: hasFilteredRankingPosition ? 'filtered-ranking' : 'this-run' }
  })
}

export function fundRankingBasis(resultFundType: AiFundType, horizonLabel: string): string {
  if (resultFundType === 'all') {
    return `本次为分类候选，分别沿用各基金类型的${horizonLabel}收益筛选结果顺序；序号仅表示类别内候选顺序，不对不同类型做综合排名。`
  }
  return `沿用${resultFundType}的${horizonLabel}历史收益筛选结果顺序；序号只描述候选在筛选结果中的位置，不是 AI 评分或未来收益预测。`
}
