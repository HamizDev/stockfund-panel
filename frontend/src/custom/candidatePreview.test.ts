import { describe, expect, it } from 'vitest'
import { DEFAULT_CANDIDATE_PREVIEW_LIMIT, previewCandidates } from './candidatePreview'

describe('candidate preview', () => {
  const ranked = Array.from({ length: 16 }, (_, index) => ({ symbol: `S${index + 1}`, rank: index + 1 }))

  it('shows the first ten rows in the existing ranking order by default', () => {
    expect(DEFAULT_CANDIDATE_PREVIEW_LIMIT).toBe(10)
    expect(previewCandidates(ranked, { expanded: false })).toEqual(ranked.slice(0, 10))
  })

  it('keeps candidates beyond ten available when expanded', () => {
    expect(previewCandidates(ranked, { expanded: true })).toEqual(ranked)
  })

  it('shows every match when a search is active, including rows beyond the first ten', () => {
    const matches = ranked.filter(item => item.symbol === 'S12' || item.symbol === 'S16')
    expect(previewCandidates(matches, { expanded: false, searchActive: true })).toEqual(matches)
  })

  it('does not change rank numbers when the caller filters rows', () => {
    const matches = ranked.filter(item => item.rank > 10)
    expect(previewCandidates(matches, { expanded: false, searchActive: true }).map(item => item.rank)).toEqual([11, 12, 13, 14, 15, 16])
  })
})
