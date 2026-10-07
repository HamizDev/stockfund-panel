// @vitest-environment jsdom
import { beforeEach, expect, it } from 'vitest'
import type { NewsFeedResponse } from '@/lib/api'
import { beijingNewsDay, readNewsFeedSessionCache, writeNewsFeedSessionCache } from './newsFeedSessionCache'

function feed(day: string): NewsFeedResponse {
  return {
    today: day,
    fetched_at: `${day}T10:00:00+08:00`,
    refreshing: true,
    related_symbols: ['601398.SH'],
    association_status: 'private holdings loaded',
    sources: [{ source: 'cls', label: '财联社', status: 'ok', fetched_at: `${day}T10:00:00+08:00`, reason: null, count: 1 }],
    summary: { today_count: 1, total_count: 1, themes: [{ label: '资本运作', count: 1 }] },
    items: [{
      id: 'article-1', title: '工商银行回购公告', summary: '公司披露回购计划', published_at: `${day}T10:00:00+08:00`,
      origins: [{ source: 'cls', label: '财联社', url: null, published_at: `${day}T10:00:00+08:00` }],
      classifications: {
        serenity: { category: 'capital', label: '资本运作', score: 18, matched: ['回购'] },
        wojianshan: { category: 'capital', label: '资本运作', score: 23, matched: ['回购'] },
      },
      sentiment: { direction: 'positive', matched: ['回购'] },
      ai_direction: {
        status: 'complete', direction: 'positive', reason: '公司回购计划构成正向事件。', evidence: ['公司披露回购计划'],
        scope: 'company', model: 'gpt-6-luna', reasoning_effort: 'high', generated_at: `${day}T10:05:00+08:00`,
      },
      associations: [{ symbol: '601398.SH', name: '工商银行', asset_type: 'stock', basis: '完整名称', direct: true }],
    }],
  }
}

beforeEach(() => sessionStorage.clear())

it('stores a bounded public projection without account associations or report fields', () => {
  const today = beijingNewsDay()
  const value = feed(today) as NewsFeedResponse & { private_report?: unknown }
  value.private_report = { content: '私有报告内容' }
  ;(value.items[0] as typeof value.items[number] & { private_report?: unknown }).private_report = '私有报告内容'

  expect(writeNewsFeedSessionCache(value)).toBe(true)
  const raw = sessionStorage.getItem('stockfund.news-feed.public.v1') || ''
  const stored = JSON.parse(raw) as { feed: Record<string, unknown> }
  expect(raw.length).toBeLessThan(1_000_000)
  expect(stored.feed.related_symbols).toBeUndefined()
  expect(stored.feed.association_status).toBeUndefined()
  expect(stored.feed.private_report).toBeUndefined()
  expect((stored.feed.items as Record<string, unknown>[])[0].private_report).toBeUndefined()

  const restored = readNewsFeedSessionCache()
  expect(restored?.items[0].title).toBe('工商银行回购公告')
  expect(restored?.items[0].ai_direction?.direction).toBe('positive')
  expect(restored?.refreshing).toBe(true)
  expect(restored?.related_symbols).toEqual([])
  expect(restored?.association_status).toBe('')
})

it('expires the snapshot at Beijing midnight', () => {
  const justBeforeMidnight = new Date('2026-10-07T15:59:59.000Z')
  const justAfterMidnight = new Date('2026-10-07T16:00:00.000Z')
  const previousDay = beijingNewsDay(justBeforeMidnight)

  expect(previousDay).toBe('2026-10-07')
  expect(beijingNewsDay(justAfterMidnight)).toBe('2026-10-08')
  expect(writeNewsFeedSessionCache(feed(previousDay), justBeforeMidnight)).toBe(true)
  expect(readNewsFeedSessionCache(justBeforeMidnight)?.today).toBe(previousDay)
  expect(readNewsFeedSessionCache(justAfterMidnight)).toBeUndefined()
  expect(sessionStorage.getItem('stockfund.news-feed.public.v1')).toBeNull()
})

it('drops malformed JSON and schema-invalid snapshots', () => {
  const today = beijingNewsDay()
  sessionStorage.setItem('stockfund.news-feed.public.v1', '{broken')
  expect(readNewsFeedSessionCache()).toBeUndefined()
  expect(sessionStorage.getItem('stockfund.news-feed.public.v1')).toBeNull()

  sessionStorage.setItem('stockfund.news-feed.public.v1', JSON.stringify({ version: 1, day: today, feed: { today, items: [] } }))
  expect(readNewsFeedSessionCache()).toBeUndefined()
  expect(sessionStorage.getItem('stockfund.news-feed.public.v1')).toBeNull()
})

it('reduces oversized snapshots to the newest rows and marks summaries truncated', () => {
  const today = beijingNewsDay()
  const oversized = feed(today)
  const template = oversized.items[0]
  const firstTimestamp = new Date(`${today}T23:50:00+08:00`).getTime()
  oversized.items = Array.from({ length: 600 }, (_, index) => ({
    ...template,
    id: `article-${index}`,
    title: `快讯标题 ${index} ${'标题'.repeat(400)}`,
    summary: '超长新闻摘要'.repeat(1_000),
    published_at: new Date(firstTimestamp - index * 60_000).toISOString(),
  }))
  oversized.summary.today_count = 600
  oversized.summary.total_count = 600

  expect(writeNewsFeedSessionCache(oversized)).toBe(true)
  const raw = sessionStorage.getItem('stockfund.news-feed.public.v1') || ''
  const restored = readNewsFeedSessionCache()
  expect(new TextEncoder().encode(raw).byteLength).toBeLessThanOrEqual(1_000_000)
  expect(restored?.cache_snapshot).toMatchObject({ total_items: 600 })
  expect(restored?.cache_snapshot?.retained_items).toBeGreaterThan(0)
  expect(restored?.cache_snapshot?.retained_items).toBeLessThan(600)
  expect(restored?.cache_snapshot?.truncated_summaries).toBe(restored?.cache_snapshot?.retained_items)
  expect(restored?.cache_snapshot?.truncated_titles).toBe(restored?.cache_snapshot?.retained_items)
  expect(restored?.items[0].id).toBe('article-0')
  expect(restored?.items[0].summary).toContain('缓存摘要已截断')
  expect(restored?.items[0].title).toContain('缓存标题已截断')
})

it('keeps the caller usable when session storage is unavailable', () => {
  expect(readNewsFeedSessionCache(new Date(), null)).toBeUndefined()
  expect(writeNewsFeedSessionCache(feed(beijingNewsDay()), new Date(), null)).toBe(false)
})
