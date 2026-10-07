import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Info, Loader2, RefreshCw, Search, Sparkles, Target, X } from 'lucide-react'
import { api, type NewsAnalysis, type NewsItem } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'

const VIEWS = [ ['all', '全市场'], ['stocks', '个股'], ['serenity', 'Serenity'], ['wojianshan', '我见山'] ] as const
const CATEGORIES = {
  serenity: [['upstream', '卡脖子·上游'], ['revalue', '估值重估'], ['institution', '机构行为'], ['trend', '产业趋势'],
    ['capital', '资本运作'], ['risk', '风险·负面'], ['other', '其他']],
  wojianshan: [['risk', '排雷·规避'], ['policy', '政策·国家级'], ['local_policy', '政策·行业地方'], ['event', '事件·重大'],
    ['theme', '产业·题材'], ['company', '公司·公告'], ['capital', '资本运作'], ['other', '其他']],
} as const
const DIRECTIONS = { positive: '正向词', negative: '负向词', mixed: '正负混合', neutral: '方向待核对' }
const timeText = (value: string | null) => value ? value.slice(5, 16).replace('T', ' ') : '尚未取得'
const badge = 'inline-flex items-center rounded border px-1.5 py-0.5 text-xs'

export function NewsPage() {
  const [params, setParams] = useSearchParams()
  const client = useQueryClient()
  const [rulesOpen, setRulesOpen] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<{ symbol: string; name: string } | null>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const rawView = params.get('view') || 'all'
  const view = VIEWS.some(([key]) => key === rawView) ? rawView : 'all'
  const category = params.get('category') || 'all'
  const source = params.get('source') || 'all'
  const scope = params.get('scope') || 'all'
  const search = params.get('q') || ''
  const reportId = params.get('report') || ''
  useEffect(() => {
    if (!reportId) return
    const previous = document.activeElement
    closeButton.current?.focus()
    return () => { if (previous instanceof HTMLElement && previous.isConnected) previous.focus() }
  }, [reportId])
  const classification = view === 'serenity' ? 'serenity' : 'wojianshan'
  const update = (fields: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    Object.entries(fields).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key))
    setParams(next, { replace: true })
  }
  const feed = useQuery({ queryKey: QK.newsFeed, queryFn: () => api.newsFeed(), staleTime: 60_000,
    refetchInterval: 120_000, refetchOnWindowFocus: false, retry: false })
  const refresh = useMutation({ mutationFn: () => api.newsFeed(true),
    onSuccess: data => { client.setQueryData(QK.newsFeed, data); setError('') },
    onError: e => setError(e instanceof Error ? e.message : '快讯刷新失败') })
  const analyze = useMutation({ mutationFn: (id: string) => api.newsAnalyze(id),
    onSuccess: data => {
      if (data.status === 'busy') { setError(data.error || '已有 AI 解读正在运行，请稍后重试'); return }
      client.setQueryData(QK.newsAnalysis(data.id), data); update({ report: data.id }); setError('')
    },
    onError: e => setError(e instanceof Error ? e.message : 'AI 解读未能启动') })
  const report = useQuery<NewsAnalysis>({ queryKey: QK.newsAnalysis(reportId), queryFn: () => api.newsAnalysis(reportId),
    enabled: !!reportId, staleTime: Infinity, retry: false, refetchOnWindowFocus: false,
    refetchInterval: query => query.state.data?.status === 'running' ? 2000 : false })
  const rows = feed.data?.items || []
  const relevant = new Set(feed.data?.related_symbols || [])
  const base = useMemo(() => rows.filter(item => {
    if (source !== 'all' && !item.origins.some(origin => origin.source === source)) return false
    if (view === 'stocks' && item.associations.length === 0) return false
    if (scope === 'mine' && !item.associations.some(stock => relevant.has(stock.symbol))) return false
    const text = `${item.title} ${item.summary} ${item.associations.map(stock => `${stock.name} ${stock.symbol}`).join(' ')}`
    return !search.trim() || text.toLowerCase().includes(search.trim().toLowerCase())
  }), [rows, source, view, scope, search, feed.data?.related_symbols])
  const visible = base.filter(item => category === 'all' || item.classifications[classification].category === category)
  const counts = Object.fromEntries(CATEGORIES[classification].map(([key]) => [key,
    base.filter(item => item.classifications[classification].category === key).length]))
  const busy = feed.isFetching || refresh.isPending
  const generate = (id: string) => { setError(''); analyze.mutate(id) }

  return <div className="flex h-full min-h-0 flex-col bg-base text-foreground">
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4 sm:px-6">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 pl-10 sm:pl-0">
        <h1 className="text-xl font-bold">市场快讯</h1>
        <p className="text-xs text-muted">财联社电报 + 东财7×24 · 分类与关联为本地规则标注</p>
      </div>
      <div className="flex items-center gap-3">
        <nav aria-label="快讯视图" className="flex rounded-md border border-border p-0.5">
          {VIEWS.map(([key, label]) => <button key={key} onClick={() => update({ view: key, category: null })}
            aria-pressed={view === key} className={cn('rounded px-2.5 py-1 text-xs sm:text-sm', view === key ? 'bg-accent text-white' : 'text-muted hover:text-foreground')}>{label}</button>)}
        </nav>
        <button aria-label="刷新快讯" title="刷新快讯（最短间隔15秒）" disabled={busy} onClick={() => refresh.mutate()}
          className="rounded p-1.5 text-muted hover:bg-elevated disabled:opacity-50"><RefreshCw size={17} className={busy ? 'animate-spin' : ''} /></button>
      </div>
    </header>
    <main className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
      <div className="mx-auto max-w-[1800px] space-y-3">
        <section className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-border bg-surface px-4 py-3 text-xs">
          <span className="inline-flex items-center gap-2 font-semibold text-foreground"><Target size={16} className="text-violet-400" />今日快讯概览</span>
          <span className="text-muted">{feed.data?.today || '北京时间'} <strong className="ml-1 font-mono text-foreground">{feed.data?.summary.today_count ?? '—'}</strong> 条去重样本</span>
          {feed.data?.summary.themes.slice(0, 3).map(theme => <span key={theme.label} className={cn(badge, 'border-accent/25 bg-accent/5 text-accent')}>{theme.label} {theme.count}</span>)}
          <span className="text-muted">当前快照 {feed.data?.summary.total_count ?? '—'} 条 · 非全市场历史档案</span>
          <button disabled={analyze.isPending || !feed.data?.summary.today_count} onClick={() => generate('daily')}
            className="ml-auto inline-flex items-center gap-1.5 rounded border border-violet-400/30 px-2.5 py-1.5 text-violet-400 hover:bg-violet-400/10 disabled:opacity-40"><Sparkles size={14} />AI 今日研判</button>
        </section>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[11px] text-muted" aria-label="快讯来源状态">
          {feed.data?.sources.map(item => <span key={item.source} title={item.reason || ''} className={cn('inline-flex items-center gap-1.5', item.status === 'stale' || item.status === 'unavailable' ? 'text-amber-400' : '')}>
            <span className={cn('h-1.5 w-1.5 rounded-full', item.status === 'ok' ? 'bg-emerald-400' : 'bg-amber-400')} />
            {item.label} {item.status === 'ok' ? '已连接' : item.status === 'empty' ? '无快讯' : item.status === 'stale' ? '连接失败，显示旧缓存' : '不可用'} · {item.count} 条 · 拉取 {timeText(item.fetched_at)}
          </span>)}
          <button onClick={() => setRulesOpen(!rulesOpen)} className="ml-auto inline-flex items-center gap-1 hover:text-foreground"><Info size={13} />分类规则</button>
        </div>
        {(view === 'serenity' || view === 'wojianshan' || rulesOpen) && <section className="rounded-lg border border-border px-4 py-2.5 text-xs leading-relaxed text-muted">
          {view === 'serenity' ? 'Serenity 产业研究视图：上游与关键环节、机构行为、估值变化、产业趋势。' : view === 'wojianshan' ? '我见山 事件研究视图：先核对风险，再观察政策、重大事件、题材与公司公告。' : '快讯分类采用本地词表初筛。'}
          {' '}类别按规则顺序匹配，风险优先；相关度分数 = 类别权重 + 每个命中词 3 分（上限 100）。
          {' '}“正向词/负向词”不等于个股利好利空；否定、引述、时间与关联关系需核对。
          {rulesOpen && <p className="mt-1">视图名称参照页面样式，词表由本项目维护，非作者本人方法或背书。仅关联新闻明确出现的完整名称或代码；短名和行业联想不自动配股。未同步维表时关联功能降级。AI 仅在点击后生成，复用当前模型和推理档；同资料、同模型的完整解读可复用缓存。</p>}
        </section>}
        <div className="flex flex-wrap items-center gap-2">
          <label className="relative min-w-40 flex-1 sm:max-w-sm"><Search size={14} className="absolute left-2.5 top-2.5 text-muted" /><input aria-label="搜索快讯" value={search} onChange={event => update({ q: event.target.value || null })}
            placeholder="搜索标题、正文、股票或 ETF" className="h-9 w-full rounded-md border border-border bg-surface pl-8 pr-3 text-xs outline-none focus:border-accent" /></label>
          <select aria-label="筛选新闻来源" value={source} onChange={event => update({ source: event.target.value, category: null })} className="h-9 rounded-md border border-border bg-surface px-2 text-xs"><option value="all">全部来源</option><option value="cls">财联社</option><option value="eastmoney">东财7×24</option></select>
          <button aria-pressed={scope === 'mine'} onClick={() => update({ scope: scope === 'mine' ? null : 'mine', category: null })}
            className={cn('h-9 rounded-md border px-3 text-xs', scope === 'mine' ? 'border-accent bg-accent/10 text-accent' : 'border-border text-muted')}>仅自选 / 登记持仓</button>
          {feed.data?.association_status && feed.data.association_status !== 'ok' && <span className="text-xs text-amber-400">{feed.data.association_status}</span>}
        </div>
        <div className="flex flex-wrap gap-1.5" aria-label="快讯分类">
          {[['all', '全部'], ...CATEGORIES[classification]].map(([key, label]) => <button key={key} aria-pressed={category === key} onClick={() => update({ category: key })}
            className={cn('rounded border px-2 py-1 text-xs', category === key ? 'border-accent/60 bg-accent/10 text-accent' : 'border-border text-muted hover:text-foreground')}>{label} <span className="ml-1 font-mono">{key === 'all' ? base.length : counts[key] || 0}</span></button>)}
        </div>
        {(error || feed.error) && <div role="alert" className="rounded border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400">{error || (feed.error instanceof Error ? feed.error.message : '快讯加载失败')}</div>}
        {feed.isLoading ? <div className="flex items-center justify-center gap-2 py-20 text-sm text-muted"><Loader2 className="animate-spin" size={18} />正在拉取公开快讯…</div>
          : visible.length === 0 ? <div className="rounded-lg border border-border py-20 text-center text-sm text-muted">{rows.length === 0 ? '暂无有效快讯，请核对来源状态后刷新' : '没有符合当前筛选的快讯'}{scope === 'mine' && <p className="mt-2 text-xs">仅匹配本地自选和登记的股票 / ETF，未命中不代表没有相关新闻。</p>}</div>
          : <section aria-label="快讯列表" className="overflow-hidden rounded-lg border border-border">
            {visible.map(item => <NewsRow key={item.id} item={item} classification={classification} onAnalyze={() => generate(item.id)} analyzing={analyze.isPending} onPreview={setPreview} />)}
          </section>}
        <p className="pb-2 text-center text-[11px] leading-relaxed text-muted">词表初筛和 AI 推断仅供继续研究；新闻未覆盖所有市场事件，关联不代表受益。请核对原文、发布时间和实际行情。</p>
      </div>
    </main>
    {reportId && <aside role="dialog" aria-modal="false" aria-label="AI 快讯解读" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); update({ report: null }) } }} className="fixed inset-y-0 right-0 z-[60] flex w-full max-w-xl flex-col border-l border-border bg-surface shadow-2xl">
      <div className="flex items-center gap-2 border-b border-border p-4"><Sparkles size={18} className="text-accent" /><h2 className="font-semibold">AI 快讯解读</h2><button ref={closeButton} aria-label="关闭解读" onClick={() => update({ report: null })} className="ml-auto rounded p-2 hover:bg-elevated"><X size={20} /></button></div>
      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        {report.data && <p className="mb-4 text-xs text-muted">{report.data.model || '当前配置模型'} · {report.data.reasoning_effort || '默认推理档'}{report.data.cache_hit && ' · 复用缓存'}{report.data.generated_at && ` · ${timeText(report.data.generated_at)}`}</p>}
        {(!report.data && report.isFetching) || report.data?.status === 'running' ? <div className="space-y-3 text-sm text-muted"><Loader2 className="animate-spin text-accent" size={24} /><p>正在核对资料并生成解读…</p><p className="text-xs">可关闭此面板；任务继续在后端运行，再次点击同条快讯会接回结果。</p></div>
          : report.data?.status === 'complete' && report.data.content ? <>
            {/* Render source names, with clickable origins supplied by the validated feed rather than model-generated URLs. */}
            <MarkdownRenderer content={report.data.content.replace(/!?\[([^\]\n]+)\]\([^)\n]+\)/g, '$1')} />
            <div className="mt-5 flex flex-wrap gap-3 text-xs">{rows.find(item => item.id === report.data?.article_id)?.origins.filter(origin => origin.url).map((origin, index) => <a key={index} href={origin.url!} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent underline">{origin.label}原文<ExternalLink size={12} /></a>)}</div>
          </>
          : <div role="alert" className="space-y-3 rounded border border-amber-400/30 p-4 text-sm text-amber-400"><p>{report.data?.error || (report.error instanceof Error ? report.error.message : '解读暂不可用，请重新点击')}</p>{report.data?.status === 'unconfigured' && <Link onClick={() => update({ report: null })} to="/settings?tab=ai" className="inline-block text-accent underline">去配置 AI</Link>}</div>}
      </div>
      <p className="border-t border-border px-5 py-3 text-[11px] text-muted">基于来源快讯样本进行研究，不执行交易。</p>
    </aside>}
    {preview && <StockPreviewDialog symbol={preview.symbol} name={preview.name} onClose={() => setPreview(null)} />}
  </div>
}

function NewsRow({ item, classification, onAnalyze, analyzing, onPreview }: {
  item: NewsItem; classification: 'serenity' | 'wojianshan'; onAnalyze: () => void; analyzing: boolean
  onPreview: (stock: { symbol: string; name: string }) => void
}) {
  const cls = item.classifications[classification]
  return <article className="border-b border-border/70 px-4 py-3.5 last:border-b-0 sm:px-5">
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
      {item.origins.map((origin, index) => <span key={`${origin.source}-${index}`} title={`该来源发布于 ${timeText(origin.published_at)}`} className={cn(badge, origin.source === 'cls' ? 'border-red-400/30 text-red-400' : 'border-amber-400/30 text-amber-400')}>{origin.label}</span>)}
      <span className={cn(badge, cls.category === 'risk' ? 'border-red-400/30 text-red-400' : cls.category === 'other' ? 'border-border' : 'border-cyan-400/25 text-cyan-400')}>{cls.label}</span>
      <span title="词表相关度，非胜率或收益" className="font-mono">{cls.score}</span><time dateTime={item.published_at} className="font-mono">{timeText(item.published_at)}</time>
      <span className={cn(badge, item.sentiment.direction === 'positive' ? 'border-emerald-400/25 text-emerald-400' : item.sentiment.direction === 'negative' ? 'border-red-400/25 text-red-400' : 'border-border')} title={item.sentiment.matched.join('、')}>{DIRECTIONS[item.sentiment.direction]}</span>
      <button onClick={onAnalyze} disabled={analyzing} className="ml-auto inline-flex items-center gap-1 text-muted hover:text-accent disabled:opacity-40"><Sparkles size={14} />解读</button>
    </div>
    <h3 className="mt-2 text-sm font-semibold leading-relaxed sm:text-[15px]">{item.title}</h3>
    {item.summary && item.summary !== item.title && <p className="mt-1 line-clamp-3 text-xs leading-6 text-muted">{item.summary}</p>}
    <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
      {cls.matched.length > 0 && <span className="mr-2">命中：{cls.matched.join('、')}</span>}
      {item.associations.length > 0 && <span>关联：</span>}
      {item.associations.map(stock => <button key={stock.symbol} title={stock.basis} onClick={() => onPreview(stock)} className="rounded border border-cyan-400/20 bg-cyan-400/5 px-1.5 py-0.5 text-cyan-400 hover:bg-cyan-400/15">{stock.asset_type === 'etf' && 'ETF · '}{stock.name} <span className="font-mono">{stock.symbol}</span>{!stock.direct && ' · 推断'}</button>)}
      {item.origins.filter(origin => origin.url).map((origin, index) => <a key={`${origin.source}-link-${index}`} href={origin.url!} target="_blank" rel="noopener noreferrer" className="ml-2 inline-flex items-center gap-1 hover:text-foreground">{origin.label}原文<ExternalLink size={11} /></a>)}
    </div>
  </article>
}
