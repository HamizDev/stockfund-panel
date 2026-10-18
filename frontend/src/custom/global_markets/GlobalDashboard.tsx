import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowUpRight, BarChart3, Gauge, Info, LineChart, RefreshCw } from 'lucide-react'
import { CanvasMarketChart } from '@/components/CanvasMarketChart'
import { MarketBoardTabs } from '@/components/dashboard/MarketBoardTabs'
import { dashboardPanel, KpiCell, SectionTitle } from '@/components/dashboard/Primitives'
import { QK } from '@/lib/queryKeys'
import { storage } from '@/lib/storage'
import { sessionViewportMemory } from '@/lib/canvas-chart/viewportMemory'
import { globalMarketClient } from './client'
import { GLOBAL_MARKET_CURRENCIES, GLOBAL_MARKET_TIMEZONES, formatGlobalMarketChange, formatGlobalInstrumentPrice, formatGlobalMarketTime } from './marketFormatters'
import { changeClass, observationStats } from './overviewMetrics'
import type { GlobalMarket, GlobalMarketChartInterval, GlobalMarketOverviewItem, GlobalMarketPageState, GlobalMarketTimeZone } from './types'
import type { MarketSnapshotItem } from './types'
import { BroadMarketSummary, CoreTechnicalPanels } from './MarketBreadthPanels'
import { GlobalIndustryPanel } from './GlobalIndustryPanel'

const defaults = { hk: '^HSI', us: '^GSPC' } as const
const intervals = [['1d', '日K'], ['5m', '5分钟'], ['1m', '1分钟']] as const
const storeKey = (market: GlobalMarket) => market === 'hk' ? 'globalDashboardHk' : 'globalDashboardUs'

function initialSelection(market: GlobalMarket): GlobalMarketPageState {
  const saved = storage.pageView[storeKey(market)].get(null)
  const value = saved && typeof saved === 'object' && !Array.isArray(saved) ? saved as Record<string, unknown> : {}
  return {
    symbol: typeof value.symbol === 'string' && /^[\w.^-]{1,20}$/.test(value.symbol) ? value.symbol : defaults[market],
    interval: intervals.some(([id]) => id === value.interval) ? value.interval as GlobalMarketChartInterval : '1d',
  }
}

function quoteTime(ms: number | null | undefined, timezone: GlobalMarketTimeZone, year = false) {
  return ms == null || !Number.isFinite(ms) ? '—' : formatGlobalMarketTime(ms / 1000, timezone, year)
}

function valuePrice(price: number | null | undefined, isIndex: boolean, market: GlobalMarket) {
  return formatGlobalInstrumentPrice(price, GLOBAL_MARKET_CURRENCIES[market], isIndex ? 'index' : undefined)
}

function ObservationList({ title, rows, market, symbol, onSelect }: {
  title: string; rows: GlobalMarketOverviewItem[]; market: GlobalMarket; symbol: string; onSelect: (symbol: string) => void
}) {
  const sorted = [...rows].sort((a, b) => (b.change_pct ?? -Infinity) - (a.change_pct ?? -Infinity))
  return <section className={dashboardPanel}>
    <SectionTitle icon={BarChart3} title={title} hint={`预设 ${rows.length} 只 · 按涨跌幅排列`} />
    <div className="space-y-1">
      {sorted.map((item, index) => <button type="button" key={item.symbol} onClick={() => onSelect(item.symbol)}
        aria-pressed={symbol === item.symbol} className={`grid w-full grid-cols-[1rem_minmax(0,1fr)_auto] items-center gap-2 rounded-lg border px-2 py-2 text-left transition-colors hover:border-accent/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${symbol === item.symbol ? 'border-accent/40 bg-accent/10' : 'border-transparent bg-elevated/55'}`}>
        <span className="font-mono text-[10px] text-muted">{index + 1}</span>
        <span className="min-w-0">
          <span className="block truncate text-xs font-medium text-foreground">{item.name}</span>
          <span className="mt-0.5 block text-[10px] text-muted">{item.symbol}</span>
          {item.status === 'loading' && <span className="block text-[10px] text-muted">{item.price == null ? '取数中' : '刷新中 · 旧缓存'}</span>}
          {item.error && <span className="block text-[10px] text-warning">{item.stale ? '旧缓存 · ' : ''}{item.error}</span>}
        </span>
        <span className="text-right font-mono text-xs tabular-nums">
          <span className={`block font-semibold ${changeClass(item.change_pct)}`}>{formatGlobalMarketChange(item.change_pct)}</span>
          <span className="mt-0.5 block text-[10px] text-muted">{valuePrice(item.price, false, market)}</span>
          <span className="mt-0.5 block text-[9px] text-muted">{quoteTime(item.quote_at_ms, GLOBAL_MARKET_TIMEZONES[market])}</span>
        </span>
      </button>)}
      {!rows.length && <p className="py-5 text-center text-xs text-muted">观察列表加载中…</p>}
    </div>
  </section>
}

export default function GlobalDashboard({ market }: { market: GlobalMarket }) {
  const [params, setParams] = useSearchParams()
  const requested = params.get('market') === market ? params.get('symbol') : null
  const [selection, setSelection] = useState(() => ({ ...initialSelection(market), ...(requested && /^[\w.^-]{1,20}$/.test(requested) ? { symbol: requested } : {}) }))
  const [picked, setPicked] = useState<MarketSnapshotItem | null>(null)
  const title = market === 'hk' ? '港股' : '美股'
  const timezone = GLOBAL_MARKET_TIMEZONES[market]
  const overview = useQuery({
    queryKey: QK.globalMarketOverview(market),
    queryFn: ({ signal }) => globalMarketClient.overview(market, signal),
    staleTime: 30_000,
    refetchInterval: query => query.state.data?.loading || query.state.data?.market_snapshot?.loading ? 2_000 : 60_000,
    refetchOnWindowFocus: false,
  })
  const chartQuery = useQuery({
    queryKey: QK.globalMarketChart(market, selection.symbol, selection.interval),
    queryFn: ({ signal }) => globalMarketClient.chart(market, selection.symbol, selection.interval, signal),
    staleTime: selection.interval === '1d' ? 60_000 : 15_000,
    refetchInterval: selection.interval === '1d' ? 60_000 : 30_000,
    refetchOnWindowFocus: false,
  })
  useEffect(() => storage.pageView[storeKey(market)].set(selection), [selection, market])
  const data = overview.data?.market === market ? overview.data : undefined
  const items = data?.items ?? []
  useEffect(() => {
    if (requested && /^[\w.^-]{1,20}$/.test(requested) && requested !== selection.symbol) setSelection(value => ({ ...value, symbol: requested }))
  }, [requested, selection.symbol])
  const chart = chartQuery.data?.market === market && chartQuery.data.symbol === selection.symbol
    && chartQuery.data.interval === selection.interval ? chartQuery.data : undefined
  const broadRows = data?.market_snapshot?.data ? Object.values(data.market_snapshot.data.ranks).flat() : []
  const selected = items.find(item => item.symbol === selection.symbol) || broadRows.find(item => item.symbol === selection.symbol)
    || (picked?.symbol === selection.symbol ? picked : undefined)
  const isIndex = selected?.kind === 'index' || chart?.kind === 'index' || selection.symbol === defaults[market]
  const name = selected?.name || chart?.name || (selection.symbol === defaults[market] ? (market === 'hk' ? '恒生指数' : '标普500') : selection.symbol)
  const stats = observationStats(items, timezone)
  const stocks = items.filter(item => item.kind === 'stock')
  const etfs = items.filter(item => item.kind === 'etf')
  const fresh = (rows: GlobalMarketOverviewItem[]) => rows.filter(item => item.status === 'ok' && !item.stale && item.price != null).length
  const price = selected ? selected.price : chart?.quote.price
  const change = selected ? selected.change_pct : chart?.quote.change_pct
  const choose = (symbol: string) => {
    setSelection(value => ({ ...value, symbol }))
    setParams({ market, symbol }, { replace: true })
    document.getElementById('global-market-chart')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }
  const chooseRank = (row: MarketSnapshotItem) => { setPicked(row); choose(row.symbol) }
  const last = chart?.bars.at(-1)

  return <div className="min-h-full bg-base p-1.5">
    <header className="relative mb-1.5 flex flex-wrap items-center justify-between gap-2 overflow-hidden rounded-card border border-border bg-gradient-to-r from-surface/90 to-surface/70 px-3 py-1.5 pl-12 md:pl-3">
      <div className="pointer-events-none absolute left-0 top-0 h-full w-1 bg-gradient-to-b from-accent to-accent/20" />
      <div className="flex items-center gap-2"><Gauge className="h-4 w-4 text-accent" /><h1 className="text-[16px] font-semibold text-foreground">{title}看板</h1><span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-muted">免费公开行情</span></div>
      <MarketBoardTabs market={market} />
      <div className="flex items-center gap-2 text-[11px] text-muted">
        <span>{data?.loading ? `取数中 ${data.completed_count ?? items.filter(item => item.status !== 'loading').length}/${items.length}` : '观察池缓存60秒'}</span>
        <button type="button" aria-label="刷新看板" disabled={overview.isFetching} onClick={() => { void overview.refetch(); void chartQuery.refetch() }} className="inline-flex items-center gap-1 rounded-btn border border-border bg-elevated px-2 py-1.5 text-secondary hover:text-foreground disabled:opacity-50"><RefreshCw className={`h-3 w-3 ${overview.isFetching ? 'animate-spin' : ''}`} />刷新</button>
        <Link to={`/markets/${market}`} className="inline-flex items-center gap-1 text-accent">{title}分析<ArrowUpRight className="h-3 w-3" /></Link>
      </div>
    </header>

    <div className="mb-1.5 flex items-start gap-2 rounded-card border border-border bg-surface px-2 py-1.5 text-[11px] text-muted"><Info className="mt-0.5 h-3 w-3 shrink-0 text-accent" /><p>市场概览优先东财公开快照；美股失败时降级为Yahoo筛选池。核心观察池与K线用Yahoo，免费行情均可能延迟。{GLOBAL_MARKET_CURRENCIES[market]} · {timezone}；指数为点位。核心观察池非全市场涨跌排行，各板块分别标注范围和报价时间。</p></div>
    {overview.isError && <div role="alert" className="mb-1.5 rounded-card border border-danger/30 bg-danger/10 p-2 text-xs text-danger">看板更新失败：{overview.error instanceof Error ? overview.error.message : '请重试'}{data ? '；保留最近可用数据。' : ''}</div>}
    {overview.isPending && <p role="status" className="mb-1.5 text-xs text-muted">正在加载指数与观察池…</p>}

    <div className={`mb-1.5 grid grid-cols-2 gap-1 ${market === 'us' ? 'xl:grid-cols-4' : ''}`}>
      {items.filter(item => item.kind === 'index').map(item => <button type="button" key={item.symbol} aria-pressed={selection.symbol === item.symbol} onClick={() => choose(item.symbol)} className={`grid min-w-0 grid-cols-[1fr_auto] gap-1 rounded-lg border bg-elevated/45 px-2 py-1.5 text-left hover:border-accent/40 ${selection.symbol === item.symbol ? 'border-accent/50' : 'border-border'}`}>
        <span className="truncate text-xs font-medium text-foreground">{item.name}</span><span className={`font-mono text-xs font-semibold ${changeClass(item.change_pct)}`}>{formatGlobalMarketChange(item.change_pct)}</span>
        <span className={`font-mono text-[11px] ${changeClass(item.change_pct)}`}>{valuePrice(item.price, true, market)}</span><span className="text-right text-[9px] text-muted">{item.error ? '暂不可用' : item.status === 'loading' ? '取数中' : quoteTime(item.quote_at_ms, timezone)}</span>
      </button>)}
    </div>

    <GlobalIndustryPanel />
    <BroadMarketSummary state={data?.market_snapshot} onSelect={chooseRank} />
    <CoreTechnicalPanels market={market} items={items} onSelect={choose} />
    <div className="mb-1.5 grid grid-cols-2 gap-1.5 lg:grid-cols-4">
      <KpiCell label="观察池涨 / 平 / 跌" value={stats.covered ? <><span className="text-bull">{stats.up}</span><span className="text-muted"> / {stats.flat} / </span><span className="text-bear">{stats.down}</span></> : '—'} sub={`${stats.date ?? '等待报价日期'} · 同日覆盖 ${stats.covered}/${stats.total}`} />
      <KpiCell label="核心指数上涨" value={stats.indexCovered ? `${stats.indexUp} / ${stats.indexCovered}` : '—'} sub={`本页 ${stats.indices} 只指数 · 其余未计入`} tone="accent" />
      <KpiCell label="股票报价覆盖" value={`${fresh(stocks)} / ${stocks.length}`} sub="预设股票观察池" />
      <KpiCell label="ETF报价覆盖" value={`${fresh(etfs)} / ${etfs.length}`} sub="预设ETF观察池" tone="accent" />
    </div>

    <div className="grid min-w-0 grid-cols-1 gap-1.5 xl:grid-cols-[minmax(0,1fr)_20rem]">
      <main className="min-w-0 space-y-1.5">
        <section className={`${dashboardPanel} scroll-mt-2`} id="global-market-chart">
          <SectionTitle icon={LineChart} title={`${name} · ${isIndex ? '指数走势' : '行情走势'}`} hint={<span className="font-mono">{selection.symbol}</span>} />
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 border-b border-border pb-2">
            <div className="flex items-baseline gap-3"><span className="font-mono text-lg font-semibold text-foreground">{valuePrice(price, isIndex, market)}</span><span className={`font-mono text-xs ${changeClass(change)}`}>{formatGlobalMarketChange(change)}</span></div>
            <div role="group" aria-label="看板K线周期" className="flex gap-1">{intervals.map(([id, label]) => <button type="button" key={id} aria-pressed={selection.interval === id} onClick={() => setSelection(value => ({ ...value, interval: id }))} className={`rounded-btn border px-2 py-1.5 text-[11px] ${selection.interval === id ? 'border-accent/40 bg-accent/10 text-accent' : 'border-border bg-elevated text-muted hover:text-foreground'}`}>{label}</button>)}</div>
          </div>
          {chartQuery.isPending && <div role="status" className="flex h-[360px] items-center justify-center text-xs text-muted">正在加载{name}K线…</div>}
          {chartQuery.isError && <div role="alert" className="rounded border border-danger/30 bg-danger/10 p-3 text-xs text-danger">图表加载失败：{chartQuery.error instanceof Error ? chartQuery.error.message : '请重试'}</div>}
          {chart && <>
            {(chart.stale || chart.error) && <p role="alert" className="mb-2 text-[11px] text-warning">旧缓存 · {chart.error || '暂未取得新行情'}</p>}
            <CanvasMarketChart viewportMemory={sessionViewportMemory} bars={chart.bars} symbol={selection.symbol} contextKey={`board:${market}:${selection.symbol}:${selection.interval}`} title={intervals.find(([id]) => id === selection.interval)?.[1]} height={360} visibleBars={80} showControls upColor="#C74040" downColor="#2D9B65" formatPrice={value => valuePrice(value, isIndex, market)} formatTime={(time, year) => formatGlobalMarketTime(time, timezone, year)} />
            <div className="mt-1 flex flex-wrap justify-between gap-1 text-[10px] text-muted"><span>图表报价 {quoteTime(chart.quote_at_ms, timezone, true)}</span><span>图表采集 {formatGlobalMarketTime(chart.fetched_at_ms / 1000, timezone, true)}</span></div>
          </>}
        </section>
        <div className="grid min-w-0 gap-1.5 md:grid-cols-2">
          <ObservationList title="股票观察" rows={stocks} market={market} symbol={selection.symbol} onSelect={choose} />
          <ObservationList title="ETF观察" rows={etfs} market={market} symbol={selection.symbol} onSelect={choose} />
        </div>
      </main>
      <aside className="min-w-0 space-y-1.5">
        <section className={dashboardPanel}>
          <SectionTitle icon={Activity} title="当前标的" hint={isIndex ? '指数 · 点位' : selected?.kind === 'etf' ? 'ETF' : selected?.kind === 'security' ? '上市证券' : '股票'} />
          <p className="mb-2 text-sm font-semibold text-foreground">{name}</p>
          <div className="space-y-1.5 text-[11px] text-muted">
            <p>报价时间 <span className="text-secondary">{quoteTime(selected ? selected.quote_at_ms : chart?.quote_at_ms, timezone, true)}</span></p>
            <p>报价来源 <span className="text-secondary">{selected && 'source' in selected && selected.source === 'eastmoney_public' ? '东财市场快照' : 'Yahoo核心观察报价'} · 图表Yahoo</span></p>
            {selected?.error && <p className="text-warning">{selected.stale ? '旧缓存 · ' : ''}{selected.error}</p>}
            <div className="border-t border-border pt-2"><p className="mb-1">最新{intervals.find(([id]) => id === selection.interval)?.[1]}K线</p><div className="grid grid-cols-2 gap-1 rounded bg-elevated/60 p-2">{(['open', 'high', 'low', 'close'] as const).map((field, index) => <p key={field}>{['开', '高', '低', '收'][index]} <span className="font-mono text-secondary">{valuePrice(last?.[field], isIndex, market)}</span></p>)}</div></div>
          </div>
        </section>
        <section className={dashboardPanel}>
          <SectionTitle icon={Info} title="行情与统计口径" />
          <ul className="space-y-2 text-[11px] leading-relaxed text-muted">
            <li>涨跌幅采用源站报告值；缺失时报“—”，不据此补成零涨跌。</li>
            <li>观察池涨跌只计同一报价日的有效记录，旧缓存不计入。涨幅排序不代表全市场榜单。</li>
            <li>观察报价缓存60秒；日K缓存15分钟，分钟K缓存30秒。免费源可能延迟、缺失或限流。</li>
            <li>源站OHLC保留拆股处理，未做分红复权。K线可能包含尚未结束的周期。</li>
            <li>{isIndex ? '指数成交量按源站提供值显示，非金额。' : '成交量单位为股，价格按市场币种展示。'}</li>
          </ul>
          {chart?.warnings.filter(warning => warning.includes('跳过')).map(warning => <p key={warning} className="mt-2 text-[11px] text-warning">{warning}</p>)}
          <Link to={`/markets/${market}`} className="mt-3 inline-flex items-center gap-1 text-xs text-accent">搜索更多股票 / ETF · 免费研究入口<ArrowUpRight className="h-3 w-3" /></Link>
        </section>
      </aside>
    </div>
  </div>
}
