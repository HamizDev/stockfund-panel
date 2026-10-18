import type { GlobalMarketOverviewItem } from './types'

export function changeClass(value: number | null | undefined) {
  return value == null || value === 0 ? 'text-muted' : value > 0 ? 'text-bull' : 'text-bear'
}

export function quoteDate(value: number | null, timezone: string) {
  if (value == null || !Number.isFinite(value)) return null
  const parts = new Intl.DateTimeFormat('en', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(value)
  const part = (type: string) => parts.find(p => p.type === type)?.value
  return `${part('year')}-${part('month')}-${part('day')}`
}

/** A fixed observation pool, never a claim about full-market breadth. */
export function observationStats(items: GlobalMarketOverviewItem[], timezone: string) {
  const securities = items.filter(item => item.kind !== 'index')
  const dates = securities.filter(item => item.status === 'ok' && !item.stale).map(item => quoteDate(item.quote_at_ms, timezone)).filter((date): date is string => !!date)
  const date = dates.sort().at(-1) ?? null
  const eligible = (item: GlobalMarketOverviewItem) => item.status === 'ok' && !item.stale
    && item.price != null && Number.isFinite(item.price) && item.change_pct != null && Number.isFinite(item.change_pct)
    && date != null && quoteDate(item.quote_at_ms, timezone) === date
  const covered = securities.filter(eligible)
  const indices = items.filter(item => item.kind === 'index' && eligible(item))
  return { date, eligible: covered, covered: covered.length, total: securities.length,
    up: covered.filter(item => item.change_pct! > 0).length,
    flat: covered.filter(item => item.change_pct === 0).length,
    down: covered.filter(item => item.change_pct! < 0).length,
    indexUp: indices.filter(item => item.change_pct! > 0).length, indexCovered: indices.length,
    indices: items.filter(item => item.kind === 'index').length,
  }
}
