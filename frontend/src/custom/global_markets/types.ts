export type GlobalMarket = 'hk' | 'us'
export type GlobalMarketChartInterval = '1d' | '5m' | '1m'
export type GlobalMarketInstrumentKind = 'stock' | 'etf' | 'index'
export type GlobalMarketCurrency = 'HKD' | 'USD'
export type GlobalMarketTimeZone = 'Asia/Hong_Kong' | 'America/New_York'

export interface GlobalMarketCatalogItem {
  symbol: string
  name: string
  market: GlobalMarket
  kind: GlobalMarketInstrumentKind
}

export interface GlobalMarketCatalogResponse {
  market: GlobalMarket
  items: GlobalMarketCatalogItem[]
  warnings: string[]
}

export interface GlobalMarketQuote {
  price: number | null
  /** 小数制，例如 0.0366 表示上涨 3.66%。 */
  change_pct: number | null
}

export interface GlobalMarketBar {
  /** 日线为当地 YYYY-MM-DD；分钟线为 Unix 秒。 */
  time: string | number
  open: number
  high: number
  low: number
  close: number
  volume: number | null
}

export interface GlobalMarketChartResponse {
  market: GlobalMarket
  symbol: string
  name: string
  kind?: GlobalMarketInstrumentKind
  currency: GlobalMarketCurrency
  timezone: GlobalMarketTimeZone
  interval: GlobalMarketChartInterval
  source: 'yahoo_public'
  price_basis: 'source_ohlc'
  fetched_at_ms: number
  quote_at_ms: number | null
  cached: boolean
  stale: boolean
  error: string | null
  warnings: string[]
  quote: GlobalMarketQuote
  bars: GlobalMarketBar[]
}

export type GlobalMarketResourceAccess = 'free_api' | 'free_web' | 'approval_required' | 'unavailable'

export interface GlobalMarketResource {
  id: string
  name: string
  url: string
  category: string
  free_scope: string
  paid_scope: string
  access: GlobalMarketResourceAccess
  /** 目录也可标记宏观数据源；macro 资源同时显示在港股和美股页。 */
  markets: string[]
}

export interface GlobalMarketResourcesResponse {
  items: GlobalMarketResource[]
}

export interface GlobalMarketPageState {
  symbol: string
  interval: GlobalMarketChartInterval
}

export interface GlobalMarketOverviewItem extends GlobalMarketCatalogItem {
  price: number | null
  change_pct: number | null
  quote_at_ms: number | null
  fetched_at_ms: number | null
  stale: boolean
  error: string | null
  status: 'loading' | 'ok' | 'error'
  refreshing?: boolean
  daily?: GlobalDailyTechnicals
  group?: string
}

export interface GlobalDailyTechnicals {
  date: string | null
  bars_count: number
  ma5: number | null
  ma20: number | null
  ma60: number | null
  above_ma5: boolean | null
  above_ma20: boolean | null
  above_ma60: boolean | null
  return_5d: number | null
  return_20d: number | null
  volume_ratio_5: number | null
  new_high_60: boolean | null
  new_low_60: boolean | null
  range_position_60: number | null
}

export interface GlobalMarketOverview {
  market: GlobalMarket
  currency: GlobalMarketCurrency
  timezone: GlobalMarketTimeZone
  scope: 'fixed_watchlist'
  source: 'yahoo_public'
  loading: boolean
  refresh_after_seconds: number
  checked_at_ms: number
  completed_count?: number
  market_snapshot?: GlobalSnapshotState
  items: GlobalMarketOverviewItem[]
}

export interface MarketSnapshotItem {
  market: GlobalMarket
  symbol: string
  name: string
  price: number | null
  change_pct: number | null
  amount: number | null
  volume: number | null
  turnover_rate: number | null
  industry: string | null
  quote_at_ms: number | null
  fetched_at_ms: number
  source: 'eastmoney_public' | 'yahoo_screener'
  kind: 'stock' | 'security' | 'etf'
  stale?: boolean
  error?: string | null
}

export interface GlobalMarketSnapshot {
  market: GlobalMarket
  scope: 'hk_mainboard' | 'us_listed_securities' | 'us_screener_pool'
  universe_label: string
  total_reported: number
  received: number
  complete: boolean
  skipped: number
  date: string | null
  source: 'eastmoney_public' | 'yahoo_screener'
  warnings?: string[]
  currency: GlobalMarketCurrency
  timezone: GlobalMarketTimeZone
  quote_at_ms: number | null
  fetched_at_ms: number
  stats: { covered: number; up: number; flat: number; down: number; strong: number; weak: number; mean: number | null; median: number | null; bins: number[] }
  ranks: { gainers: MarketSnapshotItem[]; losers: MarketSnapshotItem[]; amount: MarketSnapshotItem[]; turnover: MarketSnapshotItem[] }
  industries: { name: string; count: number; change_pct: number; leader: MarketSnapshotItem; laggard: MarketSnapshotItem }[]
  industry_covered: number
}
export interface GlobalSnapshotState {
  data: GlobalMarketSnapshot | null
  loading: boolean
  stale: boolean
  error: string | null
}
export type IndustryMode = 'auto' | 'cn' | 'us'
export interface GlobalIndustryResponse {
  market: 'cn' | 'us'
  mode: IndustryMode
  basis: 'equal_weight_representative_basket'
  source: 'panel_stock_snapshot' | 'tencent_public'
  source_note?: string
  currency: 'CNY' | 'USD'
  date: string | null
  fetched_at_ms: number | null
  loading: boolean
  stale: boolean
  error: string | null
  disclaimer: string
  auto_rule: string
  themes: { id: string; name: string; note: string; change_pct: number | null; covered: number; total: number;
    members: { symbol: string; name: string; price: number | null; change_pct: number | null; quote_at_ms: number | null;
      quote_date: string | null; basis: 'source_quote' | 'daily_reference' | 'source_reported_date'; included: boolean }[] }[]
}
export type MacroMarket = 'cn' | 'us'
export interface EtfShareSnapshot {
  symbol: string
  name: string
  date: string | null
  shares_10k: number | null
  exchange: 'SH' | 'SZ'
  source: string
  source_url: string
  fetched_at_ms: number | null
  cached: boolean
  stale: boolean
  error: string | null
}
export interface MacroReference {
  market: MacroMarket
  currency: 'CNY' | 'USD'
  date: string
  previous_date: string | null
  unit: 'percent'
  frequency: 'business_daily'
  items: { id: string; label: string; value: number | null; previous_value: number | null; change_bp: number | null }[]
  spread_bp: number | null
  source: string
  source_url: string
  fetched_at_ms: number
  cached: boolean
  stale: boolean
  error: string | null
}
