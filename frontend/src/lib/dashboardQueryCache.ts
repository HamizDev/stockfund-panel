import { dehydrate, hashKey, hydrate, replaceEqualDeep, type DehydratedState, type QueryClient } from '@tanstack/react-query'
import { storage } from './storage'

export const DASHBOARD_QUERY_GC_MS = 30 * 60_000
const MAX_AGE_MS = 24 * 3600_000
const MAX_BYTES = 1024 * 1024
type SavedQuery = DehydratedState['queries'][number]
const object = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)
const market = (value: unknown) => value === 'hk' || value === 'us'
const hasPrice = (row: unknown): row is Record<string, unknown> & { price: number } =>
  object(row) && typeof row.price === 'number' && Number.isFinite(row.price) && row.price > 0
const nullableNumber = (value: unknown) => value == null || (typeof value === 'number' && Number.isFinite(value))
const namedRow = (value: unknown): value is Record<string, unknown> => object(value)
  && typeof value.symbol === 'string' && typeof value.name === 'string'
  && nullableNumber(value.price) && nullableNumber(value.change_pct)

function validBroad(value: unknown, scope: unknown): boolean {
  const sameMarketRow = (row: unknown) => namedRow(row) && row.market === scope
  return object(value) && value.market === scope && object(value.stats) && Array.isArray(value.stats.bins)
    && value.stats.bins.length === 8 && value.stats.bins.every(x => typeof x === 'number' && Number.isFinite(x))
    && object(value.ranks) && ['gainers','losers','amount','turnover'].every(field =>
      Array.isArray(value.ranks && (value.ranks as Record<string, unknown>)[field])
      && ((value.ranks as Record<string, unknown>)[field] as unknown[]).every(sameMarketRow))
    && Array.isArray(value.industries) && value.industries.every(row => object(row)
      && typeof row.name === 'string' && sameMarketRow(row.leader) && sameMarketRow(row.laggard))
}

/** Preserve a complete snapshot only within the same market while revalidating. */
export function preserveDashboardOverview(previous: unknown, next: unknown): unknown {
  if (object(next) && market(next.market) && object(next.market_snapshot) && next.market_snapshot.data != null
    && !validBroad(next.market_snapshot.data, next.market)) {
    next = { ...next, market_snapshot: {data:null, loading:false, stale:false, error:'市场快照格式无效，等待重新获取'} }
  }
  if (!object(previous) || !object(next) || !market(next.market) || previous.market !== next.market
    || !Array.isArray(previous.items) || !Array.isArray(next.items)) return next
  const before = new Map(previous.items.filter(object).map(row => [row.symbol, row]))
  const items = next.items.map(row => {
    if (!object(row)) return row
    const old = before.get(row.symbol)
    if (hasPrice(row) || !hasPrice(old)) return row
    if (row.status === 'loading') return { ...old, refreshing: true }
    if (row.status === 'error') return { ...old, refreshing: false, stale: true, status: 'error', error: row.error }
    return row
  })
  const incomingSymbols = new Set(next.items.filter(object).map(row => row.symbol))
  const retained = next.loading === true ? [...items, ...previous.items
    .filter(row => object(row) && !incomingSymbols.has(row.symbol))
    .map(row => ({ ...row, refreshing: true }))] : items
  const broad = object(next.market_snapshot) ? next.market_snapshot : null
  const oldBroad = object(previous.market_snapshot) ? previous.market_snapshot : null
  const oldData = oldBroad && object(oldBroad.data) && oldBroad.data.market === next.market ? oldBroad.data : null
  const snapshot = !broad?.data && oldData
    ? { loading: next.loading === true, error: null, ...broad, data: oldData, stale: true } : next.market_snapshot
  return replaceEqualDeep(previous, { ...next, items: retained, market_snapshot: snapshot })
}

/** Small allowlist of public queries; never persist settings, keys or accounts. */
function slot(key: readonly unknown[], data: unknown): string | null {
  if (key[0] !== 'global-market' || !object(data)) return null
  const [_, kind, scope, symbol, interval] = key
  if (kind === 'overview' && key.length === 3 && market(scope) && data.market === scope
    && Array.isArray(data.items) && data.items.length <= 50
    && data.items.every(row => namedRow(row) && (!row.market || row.market === scope))
    && (!object(data.market_snapshot) || data.market_snapshot.data == null || validBroad(data.market_snapshot.data, scope))
    && (data.items.some(hasPrice) || (object(data.market_snapshot) && validBroad(data.market_snapshot.data, scope)))) {
    return `overview:${scope}`
  }
  if (kind === 'chart' && key.length === 5 && market(scope) && data.market === scope
    && typeof symbol === 'string' && /^[\w.^-]{1,20}$/.test(symbol) && data.symbol === symbol
    && ['1d', '5m', '1m'].includes(String(interval)) && data.interval === interval
    && typeof data.name === 'string' && Array.isArray(data.warnings) && data.warnings.every(x => typeof x === 'string')
    && object(data.quote) && nullableNumber(data.quote.price) && nullableNumber(data.quote.change_pct)
    && Array.isArray(data.bars) && data.bars.length > 0 && data.bars.length <= 2000
    && data.bars.every(bar => object(bar) && ['open','high','low','close','volume'].every(field =>
      typeof bar[field] === 'number' && Number.isFinite(bar[field])) && (typeof bar.time === 'string' || typeof bar.time === 'number'))) {
    return `chart:${scope}`
  }
  if (kind === 'industries' && key.length === 3 && ['auto','cn','us'].includes(String(scope))
    && data.mode === scope && ['cn','us'].includes(String(data.market))
    && (scope === 'auto' || data.market === scope) && Array.isArray(data.themes) && data.themes.length <= 40
    && data.themes.some(theme => object(theme) && typeof theme.covered === 'number' && theme.covered > 0)
    && data.themes.every(theme => object(theme) && typeof theme.id === 'string' && typeof theme.name === 'string' && Array.isArray(theme.members))) {
    return `industries:${scope}`
  }
  if (kind === 'macro' && key.length === 3 && scope === 'us' && data.market === 'us'
    && Array.isArray(data.items) && data.items.length <= 20 && data.items.some(row => object(row) && typeof row.value === 'number')) {
    return 'macro:us'
  }
  return null
}

function usable(query: SavedQuery, now: number): boolean {
  return object(query) && Array.isArray(query.queryKey) && object(query.state)
    && query.state.status === 'success' && Number.isFinite(query.state.dataUpdatedAt)
    && now - query.state.dataUpdatedAt >= -30_000 && now - query.state.dataUpdatedAt < MAX_AGE_MS
    && slot(query.queryKey, query.state.data) !== null
}

function readSaved(): SavedQuery[] {
  try {
    const value = storage.dashboardQueries.get(null)
    if (!object(value) || value.version !== 1 || !Array.isArray(value.queries) || value.queries.length > 8
      || new TextEncoder().encode(JSON.stringify(value)).byteLength > MAX_BYTES) return []
    return value.queries.filter((entry): entry is SavedQuery => usable(entry as SavedQuery, Date.now()))
  } catch { return [] }
}

/** Use TanStack's own dehydrated cache, with bounded public snapshots for reloads. */
export function installDashboardQueryCache(client: QueryClient): () => void {
  const saved = readSaved()
  hydrate(client, { mutations: [], queries: saved.map(query => ({
    queryKey: query.queryKey, queryHash: hashKey(query.queryKey),
    state: { ...query.state, error: null, fetchFailureReason: null, fetchMeta: null,
      fetchStatus: 'idle' as const, isInvalidated: true },
  })) })
  let timer: ReturnType<typeof setTimeout> | undefined
  const write = () => {
    timer = undefined
    const queries = dehydrate(client, { shouldDehydrateQuery: query => query.state.status === 'success'
      && slot(query.queryKey, query.state.data) !== null }).queries
      .filter(query => usable(query, Date.now())).sort((a,b) => b.state.dataUpdatedAt - a.state.dataUpdatedAt)
    const selected: SavedQuery[] = []
    const slots = new Set<string>()
    for (const query of queries) {
      const id = slot(query.queryKey, query.state.data)!
      if (slots.has(id)) continue
      const clean = { queryKey: query.queryKey, queryHash: hashKey(query.queryKey), state: {
        ...query.state, error: null, fetchFailureReason: null, fetchMeta: null, fetchStatus: 'idle' as const,
      } }
      if (new TextEncoder().encode(JSON.stringify({version:1, queries:[...selected,clean]})).byteLength <= MAX_BYTES) {
        selected.push(clean); slots.add(id)
      }
    }
    if (selected.length) storage.dashboardQueries.set({version:1, queries:selected})
  }
  const unsubscribe = client.getQueryCache().subscribe(event => {
    if (event.type !== 'updated' || event.action.type !== 'success' || slot(event.query.queryKey, event.query.state.data) === null) return
    if (timer === undefined) timer = setTimeout(write, 1000)
  })
  const flush = () => { if (timer !== undefined) { clearTimeout(timer); write() } }
  window.addEventListener('pagehide', flush)
  return () => { unsubscribe(); flush(); window.removeEventListener('pagehide', flush) }
}
