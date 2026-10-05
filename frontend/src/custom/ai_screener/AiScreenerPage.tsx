import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Activity, ArrowUpRight, Clock3, Database, RefreshCw, Search, ShieldAlert, Sparkles } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { getCandidates, formatMoney, formatPct, type Candidate, type CandidateMetrics } from './client'
import { CandidateAnalysisPanel } from './CandidateAnalysisPanel'
import { StockCandidateCard } from './StockCandidateCard'
import { researchKey, useCandidateResearch } from './useCandidateResearch'
import { candidatePriceLabel } from './candidateDisplay'
import { fetchAssistantStatus } from '../assistant/client'
import { DEFAULT_CANDIDATE_PREVIEW_LIMIT, previewCandidates } from '../candidatePreview'

function formatNumber(value: number | null, digits = 2): string {
  return value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits)
}

function quoteLabel(item: Candidate, asOf: string | null, provider: string | null): string {
  if (item.price_source === 'live') return `实时快照 · ${provider || '行情源'}`
  return `${candidatePriceLabel(item)} · ${asOf || '日期未知'}`
}

function metricRows(metrics: CandidateMetrics, assetType: 'stock' | 'etf'): { label: string; value: string }[] {
  return [
    { label: '成交额', value: formatMoney(metrics.amount) },
    { label: '换手率', value: metrics.turnover_rate == null ? '—' : `${formatNumber(metrics.turnover_rate)}%` },
    ...(assetType === 'stock' ? [{ label: '市盈率 TTM', value: formatNumber(metrics.pe_ttm) }, { label: '市净率', value: formatNumber(metrics.pb) }] : []),
    { label: 'MA5 / MA20', value: `${formatNumber(metrics.ma5)} / ${formatNumber(metrics.ma20)}` },
    { label: 'MA60', value: formatNumber(metrics.ma60) },
  ]
}

function riskNotes(item: Candidate, assetType: 'stock' | 'etf'): string[] {
  const { metrics } = item
  const notes: string[] = []
  if (metrics.close == null) notes.push('缺少有效价格')
  if (assetType === 'stock' && (metrics.pe_ttm == null || metrics.pb == null)) notes.push('估值数据不完整')
  if (assetType === 'stock' && metrics.pe_ttm != null && metrics.pe_ttm <= 0) notes.push('市盈率为非正值，需核对盈利情况')
  if (assetType === 'etf') notes.push('需另行核对跟踪指数、费率、折溢价和具体交易规则')
  if (item.price_source === 'daily' && item.price_basis === 'qfq' && metrics.close != null && metrics.ma20 != null && metrics.close < metrics.ma20) notes.push('现价低于 20 日均线')
  if (item.price_source !== 'live') notes.push('当前展示盘后价格，不能作为实时成交价')
  return notes.length ? notes : ['未发现以上数据缺失或技术提醒；仍需自行核对公告与风险。']
}

export function AiScreenerPage() {
  type AssetType = 'stock' | 'etf'
  const research = useCandidateResearch()
  const [assetType, setAssetType] = useState<AssetType>('stock')
  const [search, setSearch] = useState('')
  const [multiOnly, setMultiOnly] = useState(false)
  const [selectedByAsset, setSelectedByAsset] = useState<Record<AssetType, string | null>>({ stock: null, etf: null })
  const [visibleLimitByAsset, setVisibleLimitByAsset] = useState<Record<AssetType, number>>({ stock: DEFAULT_CANDIDATE_PREVIEW_LIMIT, etf: DEFAULT_CANDIDATE_PREVIEW_LIMIT })
  const [previewSymbol, setPreviewSymbol] = useState<string | null>(null)
  const candidateListRef = useRef<HTMLElement | null>(null)
  const selectedCandidateRef = useRef<HTMLElement | null>(null)
  const detailPanelRef = useRef<HTMLElement | null>(null)
  const query = useQuery({
    queryKey: ['ai-screener-candidates', assetType],
    queryFn: () => getCandidates(assetType),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
  const aiStatus = useQuery({ queryKey: ['ai-screener-model-status'], queryFn: fetchAssistantStatus, staleTime: 60_000 })
  const data = query.data
  const selectedSymbol = selectedByAsset[assetType]
  const searchActive = search.trim().length > 0
  const ranked = useMemo(() => (data?.items ?? []).map((item, index) => ({ item, rank: index + 1 })), [data?.items])
  const matching = useMemo(() => ranked.filter(({ item }) => {
    if (multiOnly && item.hit_count < 2) return false
    const term = search.trim().toLowerCase()
    return !term || item.symbol.toLowerCase().includes(term) || item.name.toLowerCase().includes(term)
  }), [ranked, multiOnly, search])
  const visibleLimit = visibleLimitByAsset[assetType]
  const visible = useMemo(() => previewCandidates(matching, { expanded: visibleLimit >= matching.length, searchActive, limit: visibleLimit }), [matching, visibleLimit, searchActive])
  useEffect(() => {
    if (selectedSymbol && data?.items.some(item => item.symbol === selectedSymbol)) return
    const fallback = matching[0]?.item.symbol ?? null
    if (selectedSymbol === fallback) return
    setSelectedByAsset(current => ({ ...current, [assetType]: fallback }))
  }, [assetType, data?.items, matching, selectedSymbol])
  const selectedRanked = ranked.find(({ item }) => item.symbol === selectedSymbol) ?? null
  const selected = selectedRanked?.item ?? null
  const liveCount = data?.items.filter(item => item.price_source === 'live').length ?? 0
  const scrollToDetail = () => {
    if (window.matchMedia('(max-width: 1279px)').matches) {
      detailPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }
  const scrollToCandidates = () => {
    (selectedCandidateRef.current ?? candidateListRef.current)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  const selectCandidate = (symbol: string) => {
    setSelectedByAsset(current => ({ ...current, [assetType]: symbol }))
    scrollToDetail()
  }

  return <>
    <PageHeader title="AI 选股" className="pl-14 sm:pl-5" subtitle={<span className="hidden sm:inline">股票 / ETF · 候选追踪 · 条件研究计划</span>} right={
      <button onClick={() => query.refetch()} disabled={query.isFetching} aria-label="刷新候选" className="inline-flex shrink-0 items-center gap-1.5 rounded-btn border border-border px-2 py-1.5 text-xs text-secondary hover:text-foreground disabled:opacity-50 sm:px-3">
        <RefreshCw className={`h-3.5 w-3.5 ${query.isFetching ? 'animate-spin' : ''}`} /><span className="hidden sm:inline">刷新候选</span>
      </button>
    } />
    <div className="space-y-3 px-3 py-4 sm:px-5">
      <div className="flex items-center gap-1 rounded-lg border border-border bg-surface p-1 w-fit" role="group" aria-label="候选资产类型">
        {(['stock', 'etf'] as const).map(type => <button key={type} aria-pressed={assetType === type} onClick={() => setAssetType(type)} className={`rounded-md px-5 py-1.5 text-xs font-medium ${assetType === type ? 'bg-accent/15 text-accent' : 'text-secondary hover:bg-elevated'}`}>{type === 'stock' ? '股票' : 'ETF'}</button>)}
      </div>
      <div className="rounded-xl border border-accent/25 bg-accent/[0.06] px-4 py-3 text-xs leading-5 text-secondary">
        <span className="font-medium text-foreground">研究候选：</span>{assetType === 'stock' ? '汇总已运行的股票日线策略；' : '使用默认参数独立计算支持 ETF 的日线策略；'}沿用后端排序：策略命中数降序、成交额降序、代码升序。默认显示前 {DEFAULT_CANDIDATE_PREVIEW_LIMIT} 名，搜索可查完整返回列表；右侧按需生成模型分析与条件研究计划，命中数不是 AI 评分，也不代表买入。
        {data && data.coverage.total > data.coverage.computed && <span className="mt-1 block text-warning">本次仅有 {data.coverage.computed}/{data.coverage.total} 条日线策略生成有效缓存，候选范围尚不完整。</span>}
        {aiStatus.data && !aiStatus.data.configured && <span className="mt-1 block text-warning">当前未接入 AI 模型，候选由策略规则生成。<Link className="underline" to="/settings?tab=ai">配置模型</Link>后可使用个股 AI 分析。</span>}
        {data && !data.quote.live && <span className="mt-1 block">暂无有效实时快照，当前显示盘后价。可在<Link className="mx-1 text-accent underline" to="/settings?tab=data-sources">数据源设置</Link>检查免费行情插件与实时行情开关。</span>}
      </div>

      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <Stat icon={<Sparkles className="h-4 w-4" />} label={assetType === 'stock' ? '候选股票' : '候选 ETF'} value={data ? String(data.total) : '—'} detail={assetType === 'stock' ? '来自股票策略缓存' : '独立 ETF 策略结果'} />
        <Stat icon={<Activity className="h-4 w-4" />} label="多策略命中" value={data ? String(data.items.filter(item => item.hit_count >= 2).length) : '—'} detail="至少命中两条策略" />
        <Stat icon={<Clock3 className="h-4 w-4" />} label="策略数据日期" value={data?.as_of || '—'} detail="仅同日结果参与排名" />
        <Stat icon={<Database className="h-4 w-4" />} label="实时价格覆盖" value={data ? `${liveCount}/${data.items.length}` : '—'} detail={data?.quote.live ? `${data.quote.provider} · ${new Date(data.quote.last_fetch_ms || 0).toLocaleTimeString()}` : '当前使用盘后价格'} />
      </div>

      {query.isError && <div role="alert" className="rounded-lg border border-danger/30 bg-danger/10 p-4 text-sm text-danger">{query.error instanceof Error ? query.error.message : '候选数据加载失败'}</div>}
      {data?.error && <div role="alert" className="rounded-lg border border-danger/30 bg-danger/10 p-4 text-sm text-danger">{data.error}，请检查数据与策略状态后刷新。</div>}
      {query.isLoading && <div role="status" className="rounded-xl border border-border bg-surface p-8 text-center text-sm text-muted">正在读取{assetType === 'etf' ? ' ETF 数据与独立策略结果' : '股票策略候选'}…</div>}
      {!query.isLoading && !query.isError && !data?.error && !data?.items.length && <div className="rounded-xl border border-border bg-surface p-8 text-center text-sm text-secondary">{assetType === 'stock' ? <>暂无已缓存的策略命中。先在<Link className="mx-1 text-accent underline" to="/screener">策略页</Link>运行股票日线策略。</> : <>暂无 ETF 策略候选。请检查 ETF 日线是否已同步；数据完整时也可能没有符合条件的标的。<Link className="ml-1 text-accent underline" to="/data">查看数据</Link></>}</div>}

      {!!data?.items.length && <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1fr)_370px]">
        <section ref={candidateListRef} className="min-w-0 rounded-xl border border-border bg-surface">
          <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
            <div className="flex flex-wrap items-center gap-x-2 text-sm font-semibold text-foreground">候选观察 <span className="text-xs font-normal text-muted">显示 {visible.length} / 匹配 {matching.length} 只</span></div>
            <div className="ml-auto flex items-center gap-2">
              <label className="flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1.5 text-xs text-secondary"><Search className="h-3.5 w-3.5" /><input aria-label="搜索候选" value={search} onChange={e => setSearch(e.target.value)} placeholder="代码 / 名称" className="w-24 bg-transparent text-foreground outline-none placeholder:text-muted md:w-32" /></label>
              <button onClick={() => setMultiOnly(value => !value)} className={`rounded-md border px-2.5 py-1.5 text-xs ${multiOnly ? 'border-accent/50 bg-accent/15 text-accent' : 'border-border text-secondary hover:text-foreground'}`}>多策略</button>
            </div>
          </div>
          <div className="grid gap-3 p-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr))]">
            {visible.map(({ item, rank }) => {
              const key = researchKey(assetType, item.symbol)
              return <StockCandidateCard key={key} item={item} assetType={assetType} asOf={data.as_of} provider={data.quote.provider} quoteTime={data.quote.last_fetch_ms}
                rank={rank}
                selected={selected?.symbol === item.symbol} cardRef={selected?.symbol === item.symbol ? selectedCandidateRef : undefined}
                report={research.runningKey === key ? research.drafts[key] : research.reports[key] ?? research.drafts[key]} analysisError={research.drafts[key]?.error} running={research.runningKey === key} busy={!!research.runningKey} configured={!!aiStatus.data?.configured}
                onSelect={() => selectCandidate(item.symbol)} onPreview={() => { selectCandidate(item.symbol); setPreviewSymbol(item.symbol) }}
                onAnalyze={() => { selectCandidate(item.symbol); void research.generate(item, assetType) }} />
            })}
          </div>
          {!visible.length && <div className="p-8 text-center text-sm text-muted">没有符合当前过滤条件的候选</div>}
          {matching.length > DEFAULT_CANDIDATE_PREVIEW_LIMIT && searchActive && <div role="status" className="border-t border-border px-4 py-2 text-center text-[11px] text-muted">搜索结果已显示全部 {matching.length} 只，包含前 {DEFAULT_CANDIDATE_PREVIEW_LIMIT} 名以外的候选。</div>}
          {!searchActive && matching.length > DEFAULT_CANDIDATE_PREVIEW_LIMIT && <div className="flex flex-wrap justify-center gap-2 border-t border-border px-4 py-2">
            {visibleLimit < matching.length && <button type="button" onClick={() => setVisibleLimitByAsset(current => ({ ...current, [assetType]: Math.min(current[assetType] + DEFAULT_CANDIDATE_PREVIEW_LIMIT, matching.length) }))} className="min-h-9 rounded-md px-3 text-xs text-accent hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
              再显示 10 名（当前 {visible.length}/{matching.length}）
            </button>}
            {visibleLimit > DEFAULT_CANDIDATE_PREVIEW_LIMIT && <button type="button" onClick={() => setVisibleLimitByAsset(current => ({ ...current, [assetType]: DEFAULT_CANDIDATE_PREVIEW_LIMIT }))} className="min-h-9 rounded-md px-3 text-xs text-secondary hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">收起至前 10 名</button>}
          </div>}
        </section>

        <aside ref={detailPanelRef} className="rounded-xl border border-border bg-surface p-4 xl:sticky xl:top-4 xl:self-start">
          {selected ? <>
            <button type="button" onClick={scrollToCandidates} className="mb-3 inline-flex min-h-11 items-center rounded-btn border border-border px-3 text-xs text-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent xl:hidden">返回候选</button>
            {!matching.some(({ item }) => item.symbol === selected.symbol) && <div role="status" className="mb-3 rounded-md border border-border bg-background/40 px-2.5 py-2 text-[11px] text-muted">当前查看的候选不在列表过滤结果中；清除搜索或筛选可返回其卡片。</div>}
            <div className="flex items-start justify-between gap-2"><div><div className="text-lg font-semibold text-foreground">{selected.name}</div><div className="font-mono text-xs text-muted">{selected.symbol} · 排名 #{selectedRanked?.rank ?? '—'}</div></div><span className="rounded-full border border-accent/30 bg-accent/10 px-2 py-1 text-xs text-accent">{selected.hit_count} 条策略</span></div>
            <div className="mt-4 rounded-lg border border-border bg-background/50 p-3"><div className="flex items-end justify-between"><span className="font-mono text-2xl text-foreground">{formatNumber(selected.metrics.close)}</span><span className={`font-mono ${selected.metrics.change_pct != null && selected.metrics.change_pct >= 0 ? 'text-bull' : 'text-bear'}`}>{formatPct(selected.metrics.change_pct)}</span></div><div className="mt-1 text-[11px] text-muted">{quoteLabel(selected, data.as_of, data.quote.provider)}</div></div>
            <CandidateAnalysisPanel candidate={selected} assetType={assetType} configured={!!aiStatus.data?.configured} model={aiStatus.data?.model} research={research} />
            <div className="mt-4 text-xs font-semibold text-foreground">数据指标</div><div className="mt-2 grid grid-cols-2 gap-2">{metricRows(selected.metrics, assetType).map(row => <div key={row.label} className="rounded-md border border-border bg-background/30 p-2"><div className="text-[10px] text-muted">{row.label}</div><div className="mt-1 font-mono text-xs text-foreground">{row.value}</div></div>)}</div>
            <div className="mt-5 text-xs font-semibold text-foreground">策略命中依据</div><div className="mt-2 space-y-2">{selected.strategies.map(strategy => <div key={strategy.id} className="rounded-md border border-border bg-background/30 p-2"><div className="text-xs font-medium text-accent">{strategy.name}</div><div className="mt-1 text-[11px] leading-4 text-muted">{strategy.description || '策略未提供说明，请在选股页查看完整条件。'}</div></div>)}</div>
            <div className="mt-5 flex items-center gap-1.5 text-xs font-semibold text-foreground"><ShieldAlert className="h-3.5 w-3.5 text-warning" />核对与风险</div><ul className="mt-2 list-disc space-y-1 pl-4 text-[11px] leading-4 text-secondary">{riskNotes(selected, assetType).map(note => <li key={note}>{note}</li>)}</ul>
            <div className="mt-5 flex flex-wrap gap-2"><button onClick={() => setPreviewSymbol(selected.symbol)} className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:text-foreground">查看 K 线</button><Link to={`/stock-analysis?symbol=${encodeURIComponent(selected.symbol)}&name=${encodeURIComponent(selected.name)}`} className="inline-flex items-center gap-1 rounded-btn border border-accent/35 bg-accent/10 px-3 py-1.5 text-xs text-accent hover:bg-accent/20">{assetType === 'etf' ? 'ETF 分析详情' : '个股分析详情'} <ArrowUpRight className="h-3 w-3" /></Link></div>
          </> : <div className="py-8 text-center text-sm text-muted">选择一只候选查看依据</div>}
        </aside>
      </div>}
    </div>
    <StockPreviewDialog symbol={previewSymbol} name={selected?.name} onClose={() => setPreviewSymbol(null)} navList={visible.map(({ item }) => ({ symbol: item.symbol, name: item.name }))} onNavigate={(symbol) => { setSelectedByAsset(current => ({ ...current, [assetType]: symbol })); setPreviewSymbol(symbol) }} />
  </>
}

function Stat({ icon, label, value, detail }: { icon: React.ReactNode; label: string; value: string; detail: string }) {
  return <div className="rounded-xl border border-border bg-surface px-3 py-2"><div className="flex flex-col items-start gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-2"><span className="flex items-center gap-1.5 whitespace-nowrap text-[11px] text-muted">{icon}{label}</span><span className="whitespace-nowrap font-mono text-sm font-semibold text-foreground">{value}</span></div><div className="mt-1 truncate text-[10px] text-muted">{detail}</div></div>
}
