import { type Candidate, type CandidateMetrics } from './client'

export function numberText(value: number | null | undefined, digits = 2) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—'
}

export function technicalSummary(metrics: CandidateMetrics): string {
  const finite = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v)
  const notes: string[] = []
  if (finite(metrics.ma5) && finite(metrics.ma20) && finite(metrics.ma60)) {
    notes.push(metrics.ma5 > metrics.ma20 && metrics.ma20 > metrics.ma60 ? 'MA5 > MA20 > MA60' : metrics.ma5 < metrics.ma20 && metrics.ma20 < metrics.ma60 ? '均线空头排列' : '均线未同向排列')
  }
  if (finite(metrics.macd_dif) && finite(metrics.macd_dea)) notes.push(metrics.macd_dif > metrics.macd_dea ? 'DIF 在 DEA 上方' : 'DIF 不高于 DEA')
  if (finite(metrics.kdj_k) && finite(metrics.kdj_d)) notes.push(metrics.kdj_k > metrics.kdj_d ? 'K 在 D 上方' : 'K 不高于 D')
  return notes.length ? notes.join(' · ') : '技术指标不足，待补齐日线'
}

export function candidateMissing(item: Candidate, assetType: 'stock' | 'etf'): string[] {
  const absent = (value: number | null | undefined) => typeof value !== 'number' || !Number.isFinite(value)
  const fields: string[] = []
  if (absent(item.metrics.close)) fields.push('价格')
  if (absent(item.metrics.vol_ratio_5d)) fields.push('日线量比')
  if (absent(item.metrics.macd_dif) || absent(item.metrics.kdj_k)) fields.push('MACD/KDJ')
  if (assetType === 'stock' && (absent(item.metrics.pe_ttm) || absent(item.metrics.pb))) fields.push('估值')
  if (assetType === 'etf') fields.push('折溢价/跟踪误差')
  return fields
}

export function candidatePriceLabel(item: Candidate): string {
  return item.price_source === 'live' ? '实时不复权' : item.price_basis === 'qfq' ? '前复权收盘' : '盘后价 · 口径待核对'
}
