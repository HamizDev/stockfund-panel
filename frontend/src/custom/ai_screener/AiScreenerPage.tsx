import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Activity, ArrowUpRight, Clock3, Database, RefreshCw, Search, ShieldAlert, Sparkles } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { api } from '@/lib/api'
import { getCandidates, formatMoney, formatPct, type Candidate, type CandidateMetrics } from './client'

function formatNumber(value: number | null, digits = 2): string {
  return value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits)
}

function quoteLabel(item: Candidate, asOf: string | null, provider: string | null): string {
  if (item.price_source === 'live') return `实时快照 · ${provider || '行情源'}`
  return `盘后数据 · ${asOf || '日期未知'}`
}

function metricRows(metrics: CandidateMetrics): { label: string; value: string }[] {
  return [
    { label: '成交额', value: formatMoney(metrics.amount) },
    { label: '换手率', value: metrics.turnover_rate == null ? '—' : `${formatNumber(metrics.turnover_rate)}%` },
    { label: '市盈率 TTM', value: formatNumber(metrics.pe_ttm) },
    { label: '市净率', value: formatNumber(metrics.pb) },
    { label: 'MA5 / MA20', value: `${formatNumber(metrics.ma5)} / ${formatNumber(metrics.ma20)}` },
    { label: 'MA60', value: formatNumber(metrics.ma60) },
  ]
}

function riskNotes(item: Candidate): string[] {
  const { metrics } = item
  const notes: string[] = []
  if (metrics.close == null) notes.push('缺少有效价格')
  if (metrics.pe_ttm == null || metrics.pb == null) notes.push('估值数据不完整')
  if (metrics.pe_ttm != null && metrics.pe_ttm <= 0) notes.push('市盈率为非正值，需核对盈利情况')
  if (metrics.close != null && metrics.ma20 != null && metrics.close < metrics.ma20) notes.push('现价低于 20 日均线')
  if (item.price_source !== 'live') notes.push('当前展示盘后价格，不能作为实时成交价')
  return notes.length ? notes : ['未发现以上数据缺失或技术提醒；仍需自行核对公告与风险。']
}

export function AiScreenerPage() {
  const [search, setSearch] = useState('')
  const [multiOnly, setMultiOnly] = useState(false)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [previewSymbol, setPreviewSymbol] = useState<string | null>(null)
  const query = useQuery({
    queryKey: ['ai-screener-candidates'],
    queryFn: getCandidates,
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
  const aiStatus = useQuery({ queryKey: ['ai-screener-model-status'], queryFn: api.strategyAiStatus, staleTime: 60_000 })
  const data = query.data
  const visible = useMemo(() => (data?.items ?? []).filter((item) => {
    if (multiOnly && item.hit_count < 2) return false
    const term = search.trim().toLowerCase()
    return !term || item.symbol.toLowerCase().includes(term) || item.name.toLowerCase().includes(term)
  }), [data?.items, multiOnly, search])
  useEffect(() => {
    if (!visible.length) { setSelectedSymbol(null); return }
    if (!visible.some(item => item.symbol === selectedSymbol)) setSelectedSymbol(visible[0].symbol)
  }, [visible, selectedSymbol])
  const selected = visible.find(item => item.symbol === selectedSymbol) ?? null
  const liveCount = data?.items.filter(item => item.price_source === 'live').length ?? 0

  return <>
    <PageHeader title="AI 选股" className="pl-14 sm:pl-5" subtitle={<span className="hidden sm:inline">策略候选追踪 · 可解释命中依据 · 个股 AI 分析</span>} right={
      <button onClick={() => query.refetch()} disabled={query.isFetching} aria-label="刷新候选" className="inline-flex shrink-0 items-center gap-1.5 rounded-btn border border-border px-2 py-1.5 text-xs text-secondary hover:text-foreground disabled:opacity-50 sm:px-3">
        <RefreshCw className={`h-3.5 w-3.5 ${query.isFetching ? 'animate-spin' : ''}`} /><span className="hidden sm:inline">刷新候选</span>
      </button>
    } />
    <div className="space-y-4 px-5 py-5 md:px-8">
      <div className="rounded-xl border border-accent/25 bg-accent/[0.06] px-4 py-3 text-xs leading-5 text-secondary">
        <span className="font-medium text-foreground">研究候选：</span>按已运行的日线策略命中数排序；策略命中和模型分析均不等于买卖建议。价格来源、日期和缺失字段在下方单独标明。
        {data && data.coverage.total > data.coverage.computed && <span className="mt-1 block text-warning">本次仅有 {data.coverage.computed}/{data.coverage.total} 条日线策略生成有效缓存，候选范围尚不完整。</span>}
        {aiStatus.data && !aiStatus.data.configured && <span className="mt-1 block text-warning">当前未接入 AI 模型，候选由策略规则生成。<Link className="underline" to="/settings?tab=ai">配置模型</Link>后可使用个股 AI 分析。</span>}
        {data && !data.quote.live && <span className="mt-1 block">暂无有效实时快照，当前显示盘后价。可在<Link className="mx-1 text-accent underline" to="/settings?tab=data-sources">数据源设置</Link>检查免费行情插件与实时行情开关。</span>}
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat icon={<Sparkles className="h-4 w-4" />} label="候选股票" value={data ? String(data.total) : '—'} detail="来自现有策略缓存" />
        <Stat icon={<Activity className="h-4 w-4" />} label="多策略命中" value={data ? String(data.items.filter(item => item.hit_count >= 2).length) : '—'} detail="至少命中两条策略" />
        <Stat icon={<Clock3 className="h-4 w-4" />} label="策略数据日期" value={data?.as_of || '—'} detail="仅同日结果参与排名" />
        <Stat icon={<Database className="h-4 w-4" />} label="实时价格覆盖" value={data ? `${liveCount}/${data.items.length}` : '—'} detail={data?.quote.live ? `${data.quote.provider} · ${new Date(data.quote.last_fetch_ms || 0).toLocaleTimeString()}` : '当前使用盘后价格'} />
      </div>

      {query.isError && <div role="alert" className="rounded-lg border border-danger/30 bg-danger/10 p-4 text-sm text-danger">{query.error instanceof Error ? query.error.message : '候选数据加载失败'}</div>}
      {!query.isLoading && !query.isError && !data?.items.length && <div className="rounded-xl border border-border bg-surface p-8 text-center text-sm text-secondary">暂无已缓存的策略命中。先在<Link className="mx-1 text-accent underline" to="/screener">策略页</Link>运行日线策略，再回到这里查看候选。</div>}

      {!!data?.items.length && <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_350px]">
        <section className="min-w-0 rounded-xl border border-border bg-surface">
          <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-foreground">候选观察 <span className="text-xs font-normal text-muted">{visible.length} 只</span></div>
            <div className="ml-auto flex items-center gap-2">
              <label className="flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1.5 text-xs text-secondary"><Search className="h-3.5 w-3.5" /><input aria-label="搜索候选" value={search} onChange={e => setSearch(e.target.value)} placeholder="代码 / 名称" className="w-24 bg-transparent text-foreground outline-none placeholder:text-muted md:w-32" /></label>
              <button onClick={() => setMultiOnly(value => !value)} className={`rounded-md border px-2.5 py-1.5 text-xs ${multiOnly ? 'border-accent/50 bg-accent/15 text-accent' : 'border-border text-secondary hover:text-foreground'}`}>多策略</button>
            </div>
          </div>
          <div className="grid gap-3 p-3 md:grid-cols-2 2xl:grid-cols-3">
            {visible.map(item => <button key={item.symbol} onClick={() => setSelectedSymbol(item.symbol)} className={`min-w-0 rounded-lg border p-3 text-left transition-colors ${selected?.symbol === item.symbol ? 'border-accent/65 bg-accent/[0.07]' : 'border-border bg-background/40 hover:border-accent/35'}`}>
              <div className="flex items-start justify-between gap-2"><div className="min-w-0"><div className="truncate text-sm font-semibold text-foreground">{item.name}</div><div className="font-mono text-[11px] text-muted">{item.symbol}</div></div><span className="shrink-0 rounded bg-accent/15 px-1.5 py-0.5 text-[11px] font-medium text-accent">命中 {item.hit_count}</span></div>
              <div className="mt-3 flex items-end justify-between gap-2"><span className="font-mono text-xl text-foreground">{formatNumber(item.metrics.close)}</span><span className={`font-mono text-sm ${item.metrics.change_pct == null ? 'text-muted' : item.metrics.change_pct >= 0 ? 'text-bull' : 'text-bear'}`}>{formatPct(item.metrics.change_pct)}</span></div>
              <div className="mt-2 text-[10px] text-muted">{quoteLabel(item, data.as_of, data.quote.provider)}</div>
              <div className="mt-3 flex flex-wrap gap-1">{item.strategies.slice(0, 3).map(strategy => <span key={strategy.id} className="max-w-full truncate rounded border border-border bg-elevated px-1.5 py-0.5 text-[10px] text-secondary">{strategy.name}</span>)}{item.hit_count > 3 && <span className="text-[10px] text-muted">+{item.hit_count - 3}</span>}</div>
            </button>)}
          </div>
          {!visible.length && <div className="p-8 text-center text-sm text-muted">没有符合当前过滤条件的候选</div>}
        </section>

        <aside className="rounded-xl border border-border bg-surface p-4 xl:sticky xl:top-4 xl:self-start">
          {selected ? <>
            <div className="flex items-start justify-between gap-2"><div><div className="text-lg font-semibold text-foreground">{selected.name}</div><div className="font-mono text-xs text-muted">{selected.symbol}</div></div><span className="rounded-full border border-accent/30 bg-accent/10 px-2 py-1 text-xs text-accent">{selected.hit_count} 条策略</span></div>
            <div className="mt-4 rounded-lg border border-border bg-background/50 p-3"><div className="flex items-end justify-between"><span className="font-mono text-2xl text-foreground">{formatNumber(selected.metrics.close)}</span><span className={`font-mono ${selected.metrics.change_pct != null && selected.metrics.change_pct >= 0 ? 'text-bull' : 'text-bear'}`}>{formatPct(selected.metrics.change_pct)}</span></div><div className="mt-1 text-[11px] text-muted">{quoteLabel(selected, data.as_of, data.quote.provider)}</div></div>
            <div className="mt-4 text-xs font-semibold text-foreground">数据指标</div><div className="mt-2 grid grid-cols-2 gap-2">{metricRows(selected.metrics).map(row => <div key={row.label} className="rounded-md border border-border bg-background/30 p-2"><div className="text-[10px] text-muted">{row.label}</div><div className="mt-1 font-mono text-xs text-foreground">{row.value}</div></div>)}</div>
            <div className="mt-5 text-xs font-semibold text-foreground">策略命中依据</div><div className="mt-2 space-y-2">{selected.strategies.map(strategy => <div key={strategy.id} className="rounded-md border border-border bg-background/30 p-2"><div className="text-xs font-medium text-accent">{strategy.name}</div><div className="mt-1 text-[11px] leading-4 text-muted">{strategy.description || '策略未提供说明，请在选股页查看完整条件。'}</div></div>)}</div>
            <div className="mt-5 flex items-center gap-1.5 text-xs font-semibold text-foreground"><ShieldAlert className="h-3.5 w-3.5 text-warning" />核对与风险</div><ul className="mt-2 list-disc space-y-1 pl-4 text-[11px] leading-4 text-secondary">{riskNotes(selected).map(note => <li key={note}>{note}</li>)}</ul>
            <div className="mt-5 flex flex-wrap gap-2"><button onClick={() => setPreviewSymbol(selected.symbol)} className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:text-foreground">查看 K 线</button><Link to={`/stock-analysis?symbol=${encodeURIComponent(selected.symbol)}&name=${encodeURIComponent(selected.name)}`} className="inline-flex items-center gap-1 rounded-btn border border-accent/35 bg-accent/10 px-3 py-1.5 text-xs text-accent hover:bg-accent/20">{aiStatus.data?.configured ? 'AI 个股分析' : '个股分析'} <ArrowUpRight className="h-3 w-3" /></Link></div>
          </> : <div className="py-8 text-center text-sm text-muted">选择一只候选查看依据</div>}
        </aside>
      </div>}
    </div>
    <StockPreviewDialog symbol={previewSymbol} name={selected?.name} onClose={() => setPreviewSymbol(null)} navList={visible.map(item => ({ symbol: item.symbol, name: item.name }))} onNavigate={(symbol) => { setSelectedSymbol(symbol); setPreviewSymbol(symbol) }} />
  </>
}

function Stat({ icon, label, value, detail }: { icon: React.ReactNode; label: string; value: string; detail: string }) {
  return <div className="rounded-xl border border-border bg-surface p-3"><div className="flex items-center gap-2 text-xs text-muted">{icon}{label}</div><div className="mt-2 truncate font-mono text-lg font-semibold text-foreground">{value}</div><div className="mt-1 truncate text-[10px] text-muted">{detail}</div></div>
}
