import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { CanvasMarketChart } from '@/components/CanvasMarketChart'
import { PageHeader } from '@/components/PageHeader'
import { QK } from '@/lib/queryKeys'
import { storage } from '@/lib/storage'
import { sessionViewportMemory } from '@/lib/canvas-chart/viewportMemory'
import { MarketRegionTabs } from './MarketRegionTabs'
import { globalMarketClient } from './client'
import {
  GLOBAL_MARKET_CURRENCIES,
  GLOBAL_MARKET_DEFAULTS,
  GLOBAL_MARKET_TIMEZONES,
  formatGlobalMarketChange,
  formatGlobalInstrumentPrice,
  formatGlobalMarketTime,
  globalMarketAccessLabel,
  globalMarketIntervalLabel,
  globalMarketKindLabel,
  globalMarketStorageKey,
} from './marketFormatters'
import type {
  GlobalMarket,
  GlobalMarketCatalogItem,
  GlobalMarketChartInterval,
  GlobalMarketChartResponse,
  GlobalMarketPageState,
  GlobalMarketResource,
} from './types'

const INTERVALS: { value: GlobalMarketChartInterval; label: string }[] = [
  { value: '1d', label: '日K' },
  { value: '5m', label: '5分钟' },
  { value: '1m', label: '1分钟' },
]

const marketTitles: Record<GlobalMarket, string> = { hk: '港股研究', us: '美股研究' }

function readPageState(market: GlobalMarket): GlobalMarketPageState {
  const fallback = GLOBAL_MARKET_DEFAULTS[market]
  const saved = storage.pageView[globalMarketStorageKey(market)].get(null)
  const state = saved && typeof saved === 'object' && !Array.isArray(saved)
    ? saved as Record<string, unknown>
    : null
  const symbol = typeof state?.symbol === 'string' && state.symbol.trim()
    ? state.symbol.trim()
    : fallback.symbol
  const interval = typeof state?.interval === 'string' && INTERVALS.some(item => item.value === state.interval)
    ? state.interval as GlobalMarketChartInterval
    : fallback.interval
  return {
    symbol,
    interval,
  }
}

function useDebouncedSearch(value: string, onDebounce: (value: string) => void) {
  useEffect(() => {
    const timeout = window.setTimeout(() => onDebounce(value.trim()), 280)
    return () => window.clearTimeout(timeout)
  }, [value, onDebounce])
}

function matchingChart(
  value: GlobalMarketChartResponse | undefined,
  market: GlobalMarket,
  symbol: string,
  interval: GlobalMarketChartInterval,
): value is GlobalMarketChartResponse {
  return value?.market === market && value.symbol === symbol && value.interval === interval
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请重试'
}

function marketResourcesFor(resources: GlobalMarketResource[] | undefined, market: GlobalMarket) {
  return (resources ?? []).filter(resource => resource.markets.includes(market) || resource.markets.includes('macro'))
}

function MarketResearchPageContent({ market }: { market: GlobalMarket }) {
  const [selection, setSelection] = useState(() => readPageState(market))
  const [searchText, setSearchText] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [resourcesExpanded, setResourcesExpanded] = useState(false)
  const persistSearch = useMemo(() => (value: string) => setDebouncedSearch(value), [])
  useDebouncedSearch(searchText, persistSearch)

  useEffect(() => {
    storage.pageView[globalMarketStorageKey(market)].set(selection)
  }, [market, selection])

  const catalogQuery = useQuery({
    queryKey: QK.globalMarketCatalog(market, debouncedSearch),
    queryFn: ({ signal }) => globalMarketClient.catalog(market, debouncedSearch, signal),
    staleTime: 60_000,
  })
  const chartQuery = useQuery({
    queryKey: QK.globalMarketChart(market, selection.symbol, selection.interval),
    queryFn: ({ signal }) => globalMarketClient.chart(market, selection.symbol, selection.interval, signal),
    staleTime: selection.interval === '1d' ? 60_000 : 15_000,
    refetchOnWindowFocus: false,
  })
  const resourcesQuery = useQuery({
    queryKey: QK.globalMarketResources,
    queryFn: ({ signal }) => globalMarketClient.resources(signal),
    staleTime: 86_400_000,
    enabled: resourcesExpanded,
  })

  const currency = GLOBAL_MARKET_CURRENCIES[market]
  const timezone = GLOBAL_MARKET_TIMEZONES[market]
  const chart = matchingChart(chartQuery.data, market, selection.symbol, selection.interval)
    ? chartQuery.data
    : undefined
  const items = catalogQuery.data?.market === market ? catalogQuery.data.items : []
  const searchIsCurrent = searchText.trim() === debouncedSearch
  const visibleItems = searchIsCurrent ? items : []
  const selectedItem: GlobalMarketCatalogItem | undefined = items.find(item => item.symbol === selection.symbol)
  const displayedName = chart?.name || selectedItem?.name || selection.symbol
  const kind = chart?.kind ?? selectedItem?.kind
  const formatPrice = (value: number | null | undefined) => formatGlobalInstrumentPrice(value, currency, kind)
  const resources = marketResourcesFor(resourcesQuery.data?.items, market)
  const resourceCategories = [...new Set(resources.map(item => item.category || '其他资源'))]
  const change = chart?.quote.change_pct ?? null
  const chartBars = useMemo(() => chart?.bars.map(bar => ({
    time: bar.time,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume,
  })) ?? [], [chart])

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setDebouncedSearch(searchText.trim())
  }

  function updateSelection(next: Partial<GlobalMarketPageState>) {
    setSelection(previous => ({ ...previous, ...next }))
  }

  return (
    <div className="min-h-full bg-base">
      <PageHeader
        title={marketTitles[market]}
          subtitle={<span className="hidden sm:inline">公开行情 · 行情货币 {currency} · 图表时间 {timezone}</span>}
        className="shrink-0 flex-wrap gap-x-3 gap-y-2 pl-14 pr-2 md:px-4 [&>div:first-child]:min-w-0 [&>div:first-child]:flex-wrap"
        right={(
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            <Link
              to={`/?market=${market}`}
              className="inline-flex min-h-7 items-center rounded-btn border border-border bg-surface px-2 py-1 text-[11px] text-secondary transition-colors hover:border-accent/40 hover:text-accent sm:px-2.5 sm:text-xs"
            >
              返回市场看板
            </Link>
            <MarketRegionTabs activeMarket={market} />
          </div>
        )}
      />

      <div className="mx-auto w-full max-w-[1500px] space-y-2.5 p-1.5 sm:p-2.5">
        <div className="grid min-w-0 gap-2.5 xl:grid-cols-[minmax(0,1fr)_minmax(250px,300px)]">
          <main className="min-w-0 space-y-2.5">
            <section className="rounded-card border border-border/60 bg-surface/80 p-2.5 sm:p-3">
              <div className="flex flex-wrap items-center justify-between gap-2.5">
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-semibold text-foreground">{displayedName}</h2>
                  <p className="mt-0.5 text-[11px] text-muted">{selection.symbol}{selectedItem ? ` · ${globalMarketKindLabel(selectedItem.kind)}` : ''}</p>
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                  <div className="min-w-28">
                    <div className="text-[10px] text-muted">最新报价</div>
                    <div className="mt-0.5 text-sm font-semibold tabular-nums text-foreground">
                      {formatPrice(chart?.quote.price)}
                    </div>
                  </div>
                  <div className="min-w-24">
                    <div className="text-[10px] text-muted">涨跌幅</div>
                    <div className={`mt-0.5 text-sm font-semibold tabular-nums ${change == null
                      ? 'text-muted'
                      : change > 0 ? 'text-bull' : change < 0 ? 'text-bear' : 'text-foreground'}`}>
                      {formatGlobalMarketChange(change)}
                    </div>
                  </div>
                  <button
                    type="button"
                    aria-label="刷新行情"
                    onClick={() => void chartQuery.refetch()}
                    disabled={chartQuery.isFetching}
                    className="inline-flex min-h-8 items-center rounded-btn border border-border bg-surface px-2.5 py-1.5 text-xs text-secondary transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-wait disabled:opacity-60"
                  >
                    {chartQuery.isFetching ? '正在刷新…' : '刷新'}
                  </button>
                </div>
              </div>

              <div className="mt-2.5 flex flex-wrap items-center gap-1.5" role="group" aria-label="K线周期">
                {INTERVALS.map(interval => (
                  <button
                    type="button"
                    key={interval.value}
                    aria-pressed={selection.interval === interval.value}
                    onClick={() => updateSelection({ interval: interval.value })}
                    className={`min-h-8 rounded-btn border px-2.5 py-1.5 text-xs transition-colors ${selection.interval === interval.value
                      ? 'border-accent/40 bg-accent/10 text-accent'
                      : 'border-border bg-base/60 text-secondary hover:border-accent/30 hover:text-foreground'}`}
                  >
                    {interval.label}
                  </button>
                ))}
              </div>

              <div className="mt-2.5 min-h-8 space-y-1.5">
                {chartQuery.isPending && <p role="status" className="text-xs text-muted">正在加载{globalMarketIntervalLabel(selection.interval)}行情…</p>}
                {chartQuery.isError && <p role="alert" className="rounded-md border border-danger/30 bg-danger/10 px-2.5 py-1.5 text-xs text-danger">行情加载失败：{errorText(chartQuery.error)}</p>}
                {chart?.error && <p role="alert" className="rounded-md border border-warning/30 bg-warning/10 px-2.5 py-1.5 text-xs text-warning">行情源提示：{chart.error}</p>}
                {chart?.stale && <p role="status" className="text-xs text-warning">数据源缓存已过期；下方数据为过期缓存。</p>}
                {chart?.warnings.map((warning, index) => <p key={`${warning}-${index}`} className="text-xs text-warning">提示：{warning}</p>)}
                {!chartQuery.isPending && !chartQuery.isError && chart && chart.bars.length === 0
                  && <p role="status" className="rounded-md border border-border px-2.5 py-2 text-xs text-muted">暂无{globalMarketIntervalLabel(selection.interval)}数据。</p>}
                {!chartQuery.isPending && !chartQuery.isError && !chart
                  && <p role="status" className="text-xs text-muted">当前标的行情尚未返回。</p>}
              </div>

              {chart && chart.bars.length > 0 && (
                <div className="mt-2 min-w-0 overflow-hidden rounded-card border border-border/60">
                  <CanvasMarketChart
                    bars={chartBars}
                    symbol={selection.symbol}
                    title={globalMarketIntervalLabel(selection.interval)}
                    contextKey={`${market}:${selection.symbol}:${selection.interval}`}
                  viewportMemory={sessionViewportMemory}
                    height={360}
                    visibleBars={80}
                    showControls
                    upColor="#C74040"
                    downColor="#2D9B65"
                  formatPrice={formatPrice}
                    formatTime={(time, includeYear) => formatGlobalMarketTime(time, timezone, includeYear)}
                  />
                </div>
              )}

              {chart && (
                <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted">
                  <span>数据源：{chart.source === 'yahoo_public' ? 'Yahoo Finance 公共接口' : chart.source}</span>
                  <span>价格口径：源站价格（未做分红复权）</span>
                  <span>缓存有效期：{chart.interval === '1d' ? '15 分钟（日K）' : '30 秒（分钟K）'}</span>
                  <span>更新时间：{formatGlobalMarketTime(Math.floor(chart.fetched_at_ms / 1_000), timezone, true)}</span>
                  {chart.quote_at_ms != null && <span>报价时间：{formatGlobalMarketTime(Math.floor(chart.quote_at_ms / 1_000), timezone, true)}</span>}
                  {chart.cached && <span>缓存数据</span>}
                </div>
              )}
            </section>

            <section className="rounded-card border border-border/60 bg-surface/80 p-2.5 sm:p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-xs font-semibold text-foreground">{debouncedSearch ? '搜索结果' : '预设观察样例'}</h2>
                  {!debouncedSearch && <p className="mt-0.5 text-[10px] text-muted">仅用于演示查询功能，不构成推荐或投资建议。</p>}
                </div>
                <span className="text-[10px] text-muted">名称优先显示，代码用于标识</span>
              </div>
              <form onSubmit={submitSearch} className="mt-2.5 flex gap-1.5">
                <label htmlFor="global-market-search" className="sr-only">搜索标的代码、中文或英文名称</label>
                <input
                  id="global-market-search"
                  type="search"
                  autoComplete="off"
                  value={searchText}
                  onChange={event => setSearchText(event.target.value)}
                  placeholder="输入代码、中文名或英文名"
                  className="h-8 min-w-0 flex-1 rounded-btn border border-border bg-base px-2.5 text-xs text-foreground outline-none placeholder:text-muted focus:border-accent/50"
                />
                <button type="submit" className="h-8 rounded-btn bg-accent px-3 text-xs font-medium text-white transition-colors hover:bg-accent/90">搜索</button>
              </form>
              <p className="mt-1.5 text-[10px] text-muted">搜索请求会在停止输入后提交，支持后端目录中的代码、中文名和英文名。</p>

              {!searchIsCurrent && <p role="status" className="mt-2.5 text-xs text-muted">等待输入完成…</p>}
              {searchIsCurrent && catalogQuery.isPending && <p role="status" className="mt-2.5 text-xs text-muted">正在加载标的目录…</p>}
              {searchIsCurrent && catalogQuery.isError && <p role="alert" className="mt-2.5 text-xs text-danger">标的目录加载失败：{errorText(catalogQuery.error)}</p>}
              {searchIsCurrent && catalogQuery.data?.warnings.map((warning, index) => <p key={`${warning}-${index}`} className="mt-2 text-xs text-warning">目录提示：{warning}</p>)}
              {searchIsCurrent && !catalogQuery.isPending && !catalogQuery.isError && visibleItems.length === 0
                && <p role="status" className="mt-2.5 rounded-md border border-border px-2.5 py-2 text-xs text-muted">没有匹配的标的。</p>}
              {searchIsCurrent && visibleItems.length > 0 && (
                <div className="mt-2.5 grid max-h-[20rem] gap-1.5 overflow-y-auto sm:grid-cols-2">
                  {visibleItems.map(item => (
                    <button
                      type="button"
                      key={item.symbol}
                      aria-pressed={selection.symbol === item.symbol}
                      onClick={() => updateSelection({ symbol: item.symbol })}
                      className={`flex min-w-0 items-center justify-between gap-2 rounded-md border px-2.5 py-2 text-left transition-colors ${selection.symbol === item.symbol
                        ? 'border-accent/35 bg-accent/10'
                        : 'border-border/70 bg-base/40 hover:bg-elevated/60'}`}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-xs font-medium text-foreground">{item.name}</span>
                        <span className="mt-0.5 block truncate text-[10px] text-muted">{item.symbol}</span>
                      </span>
                      <span className="shrink-0 rounded bg-elevated px-1.5 py-0.5 text-[10px] text-secondary">{globalMarketKindLabel(item.kind)}</span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </main>

          <aside className="min-w-0">
            <section className="rounded-card border border-border/60 bg-surface/80 p-2.5 sm:p-3">
              <button
                type="button"
                aria-expanded={resourcesExpanded}
                onClick={() => setResourcesExpanded(expanded => !expanded)}
                className="flex w-full items-center justify-between gap-2 text-left"
              >
                <span>
                  <span className="block text-xs font-semibold text-foreground">免费研究入口</span>
                  <span className="mt-0.5 block text-[10px] text-muted">按资源目录说明免费层和付费边界</span>
                </span>
                <span aria-hidden="true" className="text-xs text-muted">{resourcesExpanded ? '−' : '+'}</span>
              </button>
              {resourcesExpanded && (
                <div className="mt-2.5 space-y-3">
                  {resourcesQuery.isPending && <p role="status" className="text-xs text-muted">正在加载资源目录…</p>}
                  {resourcesQuery.isError && <p role="alert" className="text-xs text-danger">资源目录加载失败：{errorText(resourcesQuery.error)}</p>}
                  {!resourcesQuery.isPending && !resourcesQuery.isError && resources.length === 0
                    && <p role="status" className="text-xs text-muted">该市场暂无已配置资源。</p>}
                  {resourceCategories.map(category => (
                    <div key={category} className="space-y-1.5">
                      <h3 className="text-[11px] font-medium text-foreground">{category}</h3>
                      {resources.filter(item => (item.category || '其他资源') === category).map(resource => (
                        <article key={resource.id} className="rounded-md border border-border/70 bg-base/40 p-2">
                          <div className="flex flex-wrap items-center justify-between gap-1.5">
                            <a
                              href={resource.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-xs font-medium text-accent hover:underline"
                            >
                              {resource.name}
                            </a>
                            <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-secondary">{globalMarketAccessLabel(resource.access)}</span>
                          </div>
                          <p className="mt-1.5 text-[10px] leading-4 text-muted"><span className="font-medium text-foreground">免费层：</span>{resource.free_scope || '未说明'}</p>
                          <p className="text-[10px] leading-4 text-muted"><span className="font-medium text-foreground">付费边界：</span>{resource.paid_scope || '未说明'}</p>
                        </article>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </section>
          </aside>
      </div>
      </div>
    </div>
  )
}

export function MarketResearchPage({ market }: { market: GlobalMarket }) {
  return <MarketResearchPageContent key={market} market={market} />
}
