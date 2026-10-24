// @vitest-environment jsdom
import { QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { QK } from './queryKeys'
import { storage } from './storage'
import { installDashboardQueryCache, preserveDashboardOverview } from './dashboardQueryCache'

const clients: QueryClient[] = []
const stops: (() => void)[] = []
const client = () => { const value = new QueryClient(); clients.push(value); return value }
const install = (value: QueryClient) => { const stop = installDashboardQueryCache(value); stops.push(stop) }
const quote = (market = 'hk') => ({ market, symbol: market === 'hk' ? '^HSI' : '^GSPC',
  name: market === 'hk' ? '恒生指数' : '标普500', kind: 'index',
  price: 123, change_pct: .01, quote_at_ms: 1000, fetched_at_ms: 2000, status: 'ok', stale: false })
const overview = (market = 'hk') => ({ market, currency: market === 'hk' ? 'HKD' : 'USD', loading: false,
  checked_at_ms: Date.now(), items: [quote(market)], market_snapshot: {
    data: { market, quote_at_ms: 1000, fetched_at_ms: 2000, date: '2026-10-20',
      stats: {covered:1,bins:[0,0,0,0,1,0,0,0]}, ranks:{gainers:[],losers:[],amount:[],turnover:[]},industries:[] },
    loading: false, stale: false, error: null,
  } })
const chart = (market: 'hk' | 'us', symbol: string) => ({ market, symbol, interval: '1d',
  name:symbol, quote:{price:2,change_pct:null}, warnings:[],
  currency: market === 'hk' ? 'HKD' : 'USD', bars: [{time: 1000, open:1, high:2, low:1, close:2, volume:3}], quote_at_ms:1000 })

beforeEach(() => { vi.useFakeTimers(); localStorage.clear() })
afterEach(() => { stops.splice(0).forEach(stop => stop()); clients.splice(0).forEach(value => value.clear()); vi.useRealTimers() })

it('restores public snapshots into a new QueryClient without changing source timestamps', () => {
  const first = client(); install(first)
  const sample = overview()
  first.setQueryData(QK.globalMarketOverview('hk'), sample)
  first.setQueryData(QK.globalMarketChart('hk', '^HSI', '1d'), chart('hk','^HSI'))
  vi.advanceTimersByTime(1500)
  const next = client(); install(next)
  expect(next.getQueryData(QK.globalMarketOverview('hk'))).toEqual(sample)
  expect(next.getQueryData(QK.globalMarketChart('hk','^HSI','1d'))).toEqual(chart('hk','^HSI'))
  expect(next.getQueryState(QK.globalMarketOverview('hk'))?.isInvalidated).toBe(true)
})

it('never serializes credential status, sentiment, account or A-share private queries', () => {
  const first = client(); install(first)
  first.setQueryData(QK.globalMarketOverview('hk'), overview())
  first.setQueryData(QK.globalResearchSources, {api_key:'NEVER_PERSIST_SECRET'})
  first.setQueryData(QK.globalResearchSentiment('AAPL','news'), {private:'NEVER_PERSIST_SENTIMENT'})
  first.setQueryData(['paper','accounts'], {private:'NEVER_PERSIST_ACCOUNT'})
  first.setQueryData(['dashboard'], {private:'NEVER_PERSIST_CN'})
  vi.advanceTimersByTime(1500)
  const text = JSON.stringify(storage.dashboardQueries.get(null))
  expect(text).toContain('123')
  expect(text).not.toContain('NEVER_PERSIST')
})

it('retains only the latest chart for each market and separates market identities', () => {
  const first = client(); install(first)
  first.setQueryData(QK.globalMarketChart('hk','00700.HK','1d'),chart('hk','00700.HK'))
  vi.advanceTimersByTime(10)
  first.setQueryData(QK.globalMarketChart('hk','^HSI','1d'),chart('hk','^HSI'))
  first.setQueryData(QK.globalMarketChart('us','AAPL','1d'),chart('us','AAPL'))
  first.setQueryData(QK.globalMarketOverview('us'),overview('hk'))
  vi.advanceTimersByTime(1500)
  const next = client(); install(next)
  expect(next.getQueryData(QK.globalMarketChart('hk','00700.HK','1d'))).toBeUndefined()
  expect(next.getQueryData(QK.globalMarketChart('hk','^HSI','1d'))).toBeTruthy()
  expect(next.getQueryData(QK.globalMarketChart('us','AAPL','1d'))).toBeTruthy()
  expect(next.getQueryData(QK.globalMarketOverview('us'))).toBeUndefined()
})

it('drops snapshots after 24 hours and rejects malformed or over-size storage', () => {
  const first = client(); install(first)
  first.setQueryData(QK.globalMarketOverview('hk'),overview())
  vi.advanceTimersByTime(1500)
  vi.setSystemTime(Date.now()+24*3600_000+1)
  const expired=client(); install(expired)
  expect(expired.getQueryData(QK.globalMarketOverview('hk'))).toBeUndefined()
  storage.dashboardQueries.set({version:1,queries:'broken'})
  const broken=client(); expect(()=>install(broken)).not.toThrow()
  expect(broken.getQueryData(QK.globalMarketOverview('hk'))).toBeUndefined()
})

it('bounds serialized size and rejects a forged private hash during restore', () => {
  const first = client(); install(first)
  first.setQueryData(QK.globalMarketOverview('hk'), overview())
  first.setQueryData(QK.globalMarketChart('us','AAPL','1d'), {...chart('us','AAPL'), padding:'a'.repeat(1024*1024)})
  vi.advanceTimersByTime(1500)
  const saved = storage.dashboardQueries.get(null) as {queries:{queryHash:string}[]}
  expect(new TextEncoder().encode(JSON.stringify(saved)).byteLength).toBeLessThanOrEqual(1024*1024)
  saved.queries[0].queryHash = JSON.stringify(['paper','accounts'])
  storage.dashboardQueries.set(saved)
  const next = client(); install(next)
  expect(next.getQueryData(['paper','accounts'])).toBeUndefined()
  expect(next.getQueryData(QK.globalMarketOverview('hk'))).toBeTruthy()
  expect(next.getQueryData(QK.globalMarketChart('us','AAPL','1d'))).toBeUndefined()
})

it('rejects damaged financial data instead of hydrating a crashing chart or breadth panel', () => {
  const first = client(); install(first)
  first.setQueryData(QK.globalMarketOverview('hk'),{...overview(),market_snapshot:{data:{market:'hk'}}})
  first.setQueryData(QK.globalMarketChart('us','AAPL','1d'),{...chart('us','AAPL'),quote:undefined})
  vi.advanceTimersByTime(1500)
  const next=client(); install(next)
  expect(next.getQueryData(QK.globalMarketOverview('hk'))).toBeUndefined()
  expect(next.getQueryData(QK.globalMarketChart('us','AAPL','1d'))).toBeUndefined()
})

it('does not let an empty refresh erase the last complete same-market snapshot', () => {
  const previous=overview()
  const pending={...overview(),loading:true,items:[{...quote(),price:null,status:'loading'}],
    market_snapshot:{data:null,loading:true,stale:false,error:null}}
  const merged=preserveDashboardOverview(previous,pending) as ReturnType<typeof overview>
  expect(merged.loading).toBe(true)
  expect(merged.items[0].price).toBe(123)
  expect(merged.items[0].quote_at_ms).toBe(1000)
  expect(merged.market_snapshot.data).toEqual(previous.market_snapshot.data)
  expect(merged.market_snapshot.stale).toBe(true)
  expect(preserveDashboardOverview(previous,{...pending,market:'us'})).toEqual({...pending,market:'us'})
})

it('accepts fresh values and retains explicit errors alongside old values on failed refresh', () => {
  const previous=overview()
  const failed={...overview(), items:[{...quote(),price:null,status:'error',error:'离线'}],
    market_snapshot:{data:null,loading:false,stale:false,error:'上游离线'}}
  const merged=preserveDashboardOverview(previous,failed) as {items:{price:number;stale:boolean;error:string}[]}
  expect(merged.items[0]).toMatchObject({price:123,stale:true,error:'离线'})
  const fresh={...overview(),items:[{...quote(),price:456,quote_at_ms:3000}]}
  expect(preserveDashboardOverview(previous,fresh)).toEqual(fresh)
})

it('retains omitted same-market rows during a nonempty partial refresh', () => {
  const previous={...overview(),items:[quote(),{...quote(),symbol:'00700.HK',name:'腾讯控股',price:456}]}
  const partial={...overview(),loading:true,items:[{...quote(),price:null,status:'loading'}]}
  const merged=preserveDashboardOverview(previous,partial) as typeof previous
  expect(merged.items.map(row=>row.price)).toEqual([123,456])
  expect(merged.items[1].symbol).toBe('00700.HK')
  expect(merged.items[1].quote_at_ms).toBe(1000)
  expect((preserveDashboardOverview(previous,{...partial,loading:false}) as typeof previous).items).toHaveLength(1)
})

it('does not let a malformed successful refresh crash or erase valid breadth', () => {
  const previous=overview()
  const bad={...overview(),market_snapshot:{data:{market:'hk'},loading:false,stale:false,error:null}}
  const merged=preserveDashboardOverview(previous,bad) as ReturnType<typeof overview>
  expect(merged.market_snapshot.data).toEqual(previous.market_snapshot.data)
  expect(merged.market_snapshot.stale).toBe(true)
  expect(merged.market_snapshot.error).toContain('市场快照格式无效')
  expect((preserveDashboardOverview(undefined,bad) as ReturnType<typeof overview>).market_snapshot.data).toBeNull()
})

it('rejects another market inside persisted rank rows and industry leaders', () => {
  const first=client(); install(first)
  const mixed=overview()
  const foreign={...quote('us'),name:'美国证券'}
  const data={...mixed.market_snapshot.data,ranks:{...mixed.market_snapshot.data.ranks,gainers:[foreign]}}
  first.setQueryData(QK.globalMarketOverview('hk'),{...mixed,market_snapshot:{...mixed.market_snapshot,data}})
  vi.advanceTimersByTime(1500)
  const next=client(); install(next)
  expect(next.getQueryData(QK.globalMarketOverview('hk'))).toBeUndefined()
})
