import { describe, expect, it } from 'vitest'
import { candidateMissing, candidatePriceLabel, numberText, technicalSummary } from './candidateDisplay'
import { readCandidateReports, reportExcerpt, researchKey } from './useCandidateResearch'
import type { Candidate } from './client'

const item: Candidate = { symbol: '600000.SH', name: '样例', strategies: [], hit_count: 0, price_source: 'daily', price_basis: 'qfq', metrics: { close: 10, change_pct: 0, amount: 0, volume: 0, turnover_rate: 0, pe_ttm: null, pb: null, ma5: 11, ma20: 10, ma60: 9 } }

describe('candidate evidence display', () => {
  it('keeps real zero values and separates missing data from zero', () => {
    expect(numberText(0)).toBe('0.00')
    expect(numberText(NaN)).toBe('—')
    expect(numberText(undefined)).toBe('—')
    expect(candidateMissing(item, 'stock')).toEqual(['日线量比', 'MACD/KDJ', '估值'])
  })
  it('describes ordering without inventing crossings or a sentiment score', () => {
    expect(technicalSummary(item.metrics)).toBe('MA5 > MA20 > MA60')
    expect(technicalSummary({ ...item.metrics, macd_dif: 0.2, macd_dea: 0.1, kdj_k: 70, kdj_d: 60 })).toBe('MA5 > MA20 > MA60 · DIF 在 DEA 上方 · K 在 D 上方')
    expect(technicalSummary({ ...item.metrics, ma5: NaN })).toBe('技术指标不足，待补齐日线')
  })
  it('labels incompatible raw/live and historical adjusted prices explicitly', () => {
    expect(candidatePriceLabel(item)).toBe('前复权收盘')
    expect(candidatePriceLabel({ ...item, price_source: 'live', price_basis: 'raw' })).toBe('实时不复权')
    expect(candidatePriceLabel({ ...item, price_basis: undefined })).toContain('口径待核对')
  })
})

describe('completed analysis retention', () => {
  const report = { content: '实际模型研究', asOf: '2026-09-30', generatedAt: 123, error: '', complete: true }
  it('restores only complete validated reports and retains the old cache format', () => {
    expect(readCandidateReports(JSON.stringify({ 'stock:600000.SH': report, 'stock:bad': { ...report, error: '失败' }, 'stock:partial': { ...report, complete: false }, invalid: { ...report, content: {} } }))).toEqual({ 'stock:600000.SH': report })
    expect(readCandidateReports('garbage')).toEqual({})
    expect(readCandidateReports('null')).toEqual({})
  })
  it('keeps the most recent 24 reports and isolates stock / ETF keys', () => {
    const reports = Object.fromEntries(Array.from({ length: 30 }, (_, i) => [researchKey('stock', `${i}`), { ...report, generatedAt: i }]))
    const restored = readCandidateReports(JSON.stringify(reports))
    expect(Object.keys(restored)).toHaveLength(24)
    expect(restored['stock:0']).toBeUndefined()
    expect(restored['stock:29']).toBeTruthy()
    expect(researchKey('stock', '510300')).not.toBe(researchKey('etf', '510300'))
  })
  it('builds a bounded excerpt from the actual report text', () => {
    expect(reportExcerpt('## 研究\n**观察** 条件\n```json\nsecret\n```', 20)).toBe('研究 观察 条件')
    expect(reportExcerpt('x'.repeat(200))).toHaveLength(140)
  })
})
