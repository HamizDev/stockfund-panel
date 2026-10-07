import type { NewsFeedResponse } from '@/lib/api'

const STORAGE_KEY = 'stockfund.news-feed.public.v1'
const CACHE_VERSION = 1
const MAX_CACHE_CHARACTERS = 1_000_000
const MAX_CACHE_BYTES = 1_000_000
const CACHED_SUMMARY_LIMIT = 1_200
const CACHED_TITLE_LIMIT = 500
const SUMMARY_TRUNCATION_MARKER = '…（缓存摘要已截断）'
const TITLE_TRUNCATION_MARKER = '…（缓存标题已截断）'

export interface NewsFeedCacheSnapshot {
  total_items: number
  retained_items: number
  truncated_summaries: number
  truncated_titles: number
}

export type SessionCachedNewsFeed = NewsFeedResponse & { cache_snapshot?: NewsFeedCacheSnapshot }

type PublicNewsFeed = Omit<NewsFeedResponse, 'related_symbols' | 'association_status'> & {
  cache_snapshot?: NewsFeedCacheSnapshot
}

interface StoredNewsFeedCache {
  version: typeof CACHE_VERSION
  day: string
  feed: PublicNewsFeed & { cache_snapshot: NewsFeedCacheSnapshot }
}

function getSessionStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

function removeCache(storage: Storage) {
  try { storage.removeItem(STORAGE_KEY) } catch { /* Storage is optional. */ }
}

export function beijingNewsDay(date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(date)
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find(part => part.type === type)?.value || ''
  return `${value('year')}-${value('month')}-${value('day')}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isString(value: unknown): value is string { return typeof value === 'string' }
function isNullableString(value: unknown): value is string | null { return value === null || isString(value) }
function isStringArray(value: unknown): value is string[] { return Array.isArray(value) && value.every(isString) }

function isCacheSnapshot(value: unknown): value is NewsFeedCacheSnapshot {
  return isRecord(value) && ['total_items', 'retained_items', 'truncated_summaries', 'truncated_titles']
    .every(key => typeof value[key] === 'number' && Number.isInteger(value[key]) && (value[key] as number) >= 0)
}

function truncateText(value: string, limit: number, marker: string): { value: string; truncated: boolean } {
  if (value.length <= limit) return { value, truncated: false }
  const end = Math.max(0, limit - marker.length)
  return { value: `${value.slice(0, end)}${marker}`, truncated: true }
}

function isClassification(value: unknown): boolean {
  return isRecord(value) && isString(value.category) && isString(value.label)
    && typeof value.score === 'number' && Number.isFinite(value.score) && isStringArray(value.matched)
}

function isNewsItem(value: unknown): boolean {
  if (!isRecord(value) || !isString(value.id) || !isString(value.title) || !isString(value.summary)
    || !isString(value.published_at) || !Array.isArray(value.origins) || !isRecord(value.classifications)
    || !isRecord(value.sentiment) || !Array.isArray(value.associations)) return false

  const originsValid = value.origins.every(origin => isRecord(origin) && isString(origin.source)
    && isString(origin.label) && isNullableString(origin.url) && isString(origin.published_at))
  const direction = value.sentiment.direction
  const sentimentValid = ['positive', 'negative', 'mixed', 'neutral'].includes(String(direction))
    && isStringArray(value.sentiment.matched)
  const associationsValid = value.associations.every(stock => isRecord(stock) && isString(stock.symbol)
    && isString(stock.name) && (stock.asset_type === 'stock' || stock.asset_type === 'etf')
    && isString(stock.basis) && typeof stock.direct === 'boolean')
  const aiDirection = value.ai_direction
  const aiDirectionValid = aiDirection === undefined || (isRecord(aiDirection)
    && aiDirection.status === 'complete'
    && ['positive', 'negative', 'neutral', 'mixed', 'uncertain'].includes(String(aiDirection.direction))
    && isString(aiDirection.reason) && isStringArray(aiDirection.evidence)
    && ['market', 'industry', 'company', 'unclear'].includes(String(aiDirection.scope))
    && isString(aiDirection.model) && (aiDirection.reasoning_effort === 'high' || aiDirection.reasoning_effort === 'max')
    && isString(aiDirection.generated_at))
  return originsValid && sentimentValid && associationsValid
    && aiDirectionValid
    && isClassification(value.classifications.serenity) && isClassification(value.classifications.wojianshan)
}

function isPublicNewsFeed(value: unknown): value is PublicNewsFeed {
  if (!isRecord(value) || !isString(value.today) || !isNullableString(value.fetched_at)
    || !Array.isArray(value.items) || !Array.isArray(value.sources) || !isRecord(value.summary)) return false
  if (value.refreshing !== undefined && typeof value.refreshing !== 'boolean') return false
  if (value.cache_snapshot !== undefined && !isCacheSnapshot(value.cache_snapshot)) return false

  const sourcesValid = value.sources.every(source => isRecord(source) && isString(source.source)
    && isString(source.label) && ['ok', 'empty', 'unavailable', 'stale'].includes(String(source.status))
    && isNullableString(source.fetched_at) && isNullableString(source.reason)
    && typeof source.count === 'number' && Number.isFinite(source.count))
  const summaryValid = typeof value.summary.today_count === 'number' && Number.isFinite(value.summary.today_count)
    && typeof value.summary.total_count === 'number' && Number.isFinite(value.summary.total_count)
    && Array.isArray(value.summary.themes) && value.summary.themes.every(theme => isRecord(theme)
      && isString(theme.label) && typeof theme.count === 'number' && Number.isFinite(theme.count))
  return sourcesValid && summaryValid && value.items.every(isNewsItem)
}

function storedCache(value: unknown): value is StoredNewsFeedCache {
  return isRecord(value) && value.version === CACHE_VERSION && isString(value.day)
    && isPublicNewsFeed(value.feed) && isRecord(value.feed) && isCacheSnapshot(value.feed.cache_snapshot)
}

function publicProjection(feed: NewsFeedResponse, items: NewsFeedResponse['items'], truncate: boolean): PublicNewsFeed {
  const text = (value: string, limit: number, marker: string) => truncate
    ? truncateText(value, limit, marker).value
    : value
  const fieldText = (value: string, limit: number) => text(value, limit, '…（缓存字段截断）')
  return {
    items: items.map(item => ({
      id: item.id,
      title: text(item.title, CACHED_TITLE_LIMIT, TITLE_TRUNCATION_MARKER),
      summary: text(item.summary, CACHED_SUMMARY_LIMIT, SUMMARY_TRUNCATION_MARKER),
      published_at: item.published_at,
      origins: item.origins.slice(0, 4).map(origin => ({
        source: fieldText(origin.source, 80), label: fieldText(origin.label, 80),
        url: origin.url && origin.url.length <= 2_048 ? origin.url : null,
        published_at: origin.published_at,
      })),
      classifications: {
        serenity: {
          category: item.classifications.serenity.category,
          label: fieldText(item.classifications.serenity.label, 80),
          score: item.classifications.serenity.score,
          matched: item.classifications.serenity.matched.slice(0, 12).map(value => fieldText(value, 80)),
        },
        wojianshan: {
          category: item.classifications.wojianshan.category,
          label: fieldText(item.classifications.wojianshan.label, 80),
          score: item.classifications.wojianshan.score,
          matched: item.classifications.wojianshan.matched.slice(0, 12).map(value => fieldText(value, 80)),
        },
      },
      sentiment: { direction: item.sentiment.direction, matched: item.sentiment.matched.slice(0, 12).map(value => fieldText(value, 80)) },
      ...(item.ai_direction ? { ai_direction: {
        status: item.ai_direction.status,
        direction: item.ai_direction.direction,
        reason: fieldText(item.ai_direction.reason, 500),
        evidence: item.ai_direction.evidence.slice(0, 6).map(value => fieldText(value, 300)),
        scope: item.ai_direction.scope,
        model: fieldText(item.ai_direction.model, 100),
        reasoning_effort: item.ai_direction.reasoning_effort,
        generated_at: item.ai_direction.generated_at,
      } } : {}),
      associations: item.associations.slice(0, 12).map(stock => ({
        symbol: fieldText(stock.symbol, 80), name: fieldText(stock.name, 100), asset_type: stock.asset_type,
        basis: fieldText(stock.basis, 200), direct: stock.direct,
      })),
    })),
    sources: feed.sources.slice(0, 20).map(source => ({
      source: fieldText(source.source, 80), label: fieldText(source.label, 100), status: source.status,
      fetched_at: source.fetched_at,
      reason: source.reason === null ? null : fieldText(source.reason, 500),
      count: source.count,
    })),
    fetched_at: feed.fetched_at,
    ...(typeof feed.refreshing === 'boolean' ? { refreshing: feed.refreshing } : {}),
    today: feed.today,
    summary: {
      today_count: feed.summary.today_count,
      total_count: feed.summary.total_count,
      themes: feed.summary.themes.slice(0, 20).map(theme => ({ label: fieldText(theme.label, 100), count: theme.count })),
    },
  }
}

function newestItems(items: NewsFeedResponse['items'], count: number): NewsFeedResponse['items'] {
  if (count >= items.length) return items
  const keep = new Set(items.map((item, index) => ({ index, time: Date.parse(item.published_at) || 0 }))
    .sort((a, b) => b.time - a.time || a.index - b.index)
    .slice(0, count).map(item => item.index))
  return items.filter((_, index) => keep.has(index))
}

function snapshotMetadata(feed: NewsFeedResponse, items: NewsFeedResponse['items'], truncate: boolean): NewsFeedCacheSnapshot {
  return {
    total_items: feed.items.length,
    retained_items: items.length,
    truncated_summaries: truncate ? items.filter(item => item.summary.length > CACHED_SUMMARY_LIMIT).length : 0,
    truncated_titles: truncate ? items.filter(item => item.title.length > CACHED_TITLE_LIMIT).length : 0,
  }
}

function withinCacheLimit(serialized: string): boolean {
  try {
    return serialized.length <= MAX_CACHE_CHARACTERS
      && new TextEncoder().encode(serialized).byteLength <= MAX_CACHE_BYTES
  } catch {
    return false
  }
}

function serializeCache(feed: NewsFeedResponse, day: string, retained: NewsFeedResponse['items'], truncate: boolean): string | undefined {
  const publicFeed = publicProjection(feed, retained, truncate)
  const snapshot = snapshotMetadata(feed, retained, truncate)
  const serialized = JSON.stringify({
    version: CACHE_VERSION,
    day,
    feed: { ...publicFeed, cache_snapshot: snapshot },
  } satisfies StoredNewsFeedCache)
  return withinCacheLimit(serialized) ? serialized : undefined
}

export function readNewsFeedSessionCache(now = new Date(), storage = getSessionStorage()): SessionCachedNewsFeed | undefined {
  if (!storage) return undefined
  let raw: string | null
  try { raw = storage.getItem(STORAGE_KEY) } catch { return undefined }
  if (!raw) return undefined
  if (!withinCacheLimit(raw)) { removeCache(storage); return undefined }

  let parsed: unknown
  try { parsed = JSON.parse(raw) } catch { removeCache(storage); return undefined }
  if (!storedCache(parsed)) { removeCache(storage); return undefined }
  const today = beijingNewsDay(now)
  if (parsed.day !== today || parsed.feed.today !== today) { removeCache(storage); return undefined }

  // Private association metadata is supplied only by the live API response.
  return { ...parsed.feed, related_symbols: [], association_status: '' }
}

export function writeNewsFeedSessionCache(
  feed: NewsFeedResponse,
  now = new Date(),
  storage = getSessionStorage(),
): boolean {
  if (!storage || !isPublicNewsFeed(feed) || !isStringArray(feed.related_symbols)
    || !isString(feed.association_status)) return false

  const today = beijingNewsDay(now)
  if (feed.today !== today) return false

  try {
    let serialized = serializeCache(feed, today, feed.items, false)
    if (!serialized) {
      let low = 0
      let high = feed.items.length
      while (low <= high) {
        const middle = Math.floor((low + high) / 2)
        const candidate = serializeCache(feed, today, newestItems(feed.items, middle), true)
        if (candidate) {
          serialized = candidate
          low = middle + 1
        } else {
          high = middle - 1
        }
      }
    }
    if (!serialized) return false
    storage.setItem(STORAGE_KEY, serialized)
    return true
  } catch {
    // Keep any previous snapshot intact if storage is full or blocked.
    return false
  }
}
