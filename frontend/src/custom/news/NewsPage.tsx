import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Info, Loader2, RefreshCw, Search, Sparkles, Target, X } from 'lucide-react'
import { ApiError, api, type NewsAnalysis, type NewsDirectionJob, type NewsItem } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { beijingNewsDay, readNewsFeedSessionCache, writeNewsFeedSessionCache, type SessionCachedNewsFeed } from './newsFeedSessionCache'

const VIEWS = [ ['all', '全市场'], ['stocks', '个股'], ['serenity', 'Serenity'], ['wojianshan', '我见山'] ] as const
const CATEGORIES = {
  serenity: [['upstream', '卡脖子·上游'], ['revalue', '估值重估'], ['institution', '机构行为'], ['trend', '产业趋势'],
    ['capital', '资本运作'], ['risk', '风险·负面'], ['other', '其他']],
  wojianshan: [['risk', '排雷·规避'], ['policy', '政策·国家级'], ['local_policy', '政策·行业地方'], ['event', '事件·重大'],
    ['theme', '产业·题材'], ['company', '公司·公告'], ['capital', '资本运作'], ['other', '其他']],
} as const
const DIRECTIONS = { positive: '正向词', negative: '负向词', mixed: '正负混合', neutral: '方向待核对' }
const AI_DIRECTIONS = {
  positive: 'AI 偏正向', negative: 'AI 偏负向', neutral: 'AI 中性', mixed: 'AI 正负混合', uncertain: 'AI 证据不足',
} as const
const AI_SCOPES = { market: '市场', industry: '行业', company: '公司', unclear: '范围待核对' } as const
const timeText = (value: string | null) => value ? value.slice(5, 16).replace('T', ' ') : '尚未取得'
const badge = 'inline-flex items-center rounded border px-1.5 py-0.5 text-xs'
const DIRECTION_JOB_STORAGE_KEY = 'stockfund.news-direction-job.v1'
type NewsDirectionEffort = 'high' | 'max'
interface NewsDirectionSession { id: string; effort: NewsDirectionEffort }

function readNewsDirectionSession(): NewsDirectionSession | null {
  try {
    const raw = window.sessionStorage.getItem(DIRECTION_JOB_STORAGE_KEY)
    if (!raw || raw.length > 512) return null
    const value: unknown = JSON.parse(raw)
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
    const record = value as Record<string, unknown>
    if (typeof record.id !== 'string' || !record.id || record.id.length > 256
      || (record.effort !== 'high' && record.effort !== 'max')) return null
    return { id: record.id, effort: record.effort }
  } catch {
    return null
  }
}

function saveNewsDirectionSession(value: NewsDirectionSession) {
  try { window.sessionStorage.setItem(DIRECTION_JOB_STORAGE_KEY, JSON.stringify({ id: value.id, effort: value.effort })) } catch { /* Session storage is optional. */ }
}

function clearNewsDirectionSession() {
  try { window.sessionStorage.removeItem(DIRECTION_JOB_STORAGE_KEY) } catch { /* Session storage is optional. */ }
}

function sourceStatusLabel(item: { status: string; reason: string | null }, refreshing: boolean | undefined): string {
  if (item.status === 'ok') return '已连接'
  if (item.status === 'empty') return '无快讯'
  const checking = item.reason?.includes('正在核对来源') === true || (refreshing === true && !item.reason)
  if ((item.status === 'stale' || item.status === 'unavailable') && checking) return '缓存，后台核对'
  return item.status === 'stale' ? '连接失败，显示旧缓存' : '不可用'
}

export function NewsPage() {
  const [params, setParams] = useSearchParams()
  const client = useQueryClient()
  const [rulesOpen, setRulesOpen] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<{ symbol: string; name: string } | null>(null)
  const [sessionFeed] = useState(() => readNewsFeedSessionCache())
  const [directionSession, setDirectionSession] = useState<NewsDirectionSession>(() => readNewsDirectionSession() || { id: '', effort: 'high' })
  const [directionEffort, setDirectionEffort] = useState<NewsDirectionEffort>(() => directionSession.effort)
  const [immediateDirectionResult, setImmediateDirectionResult] = useState<NewsDirectionJob | null>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const refreshedDirectionJob = useRef('')
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
  const feedDay = beijingNewsDay()
  const feed = useQuery<SessionCachedNewsFeed>({ queryKey: QK.newsFeed, queryFn: async () => {
      const data = await api.newsFeed()
      writeNewsFeedSessionCache(data)
      return data
    }, initialData: () => sessionFeed,
    staleTime: query => query.state.data?.today === feedDay ? 60_000 : 0,
    refetchInterval: query => query.state.data?.refreshing ? 15_000 : 120_000,
    refetchIntervalInBackground: true,
    refetchOnMount: query => query.state.data?.today !== feedDay,
    refetchOnWindowFocus: false, retry: false })
  const refresh = useMutation({ mutationFn: () => api.newsFeed(true),
    onSuccess: data => { writeNewsFeedSessionCache(data); client.setQueryData(QK.newsFeed, data); setError('') },
    onError: e => setError(e instanceof Error ? e.message : '快讯刷新失败') })
  const directionJob = useQuery<NewsDirectionJob>({
    queryKey: QK.newsDirectionJob(directionSession.id || 'none'),
    queryFn: () => api.newsDirectionJob(directionSession.id),
    enabled: !!directionSession.id,
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: query => query.state.data?.status === 'running' || !!directionSession.id ? 2_000 : false,
    refetchIntervalInBackground: true,
  })
  const startDirection = useMutation({
    mutationFn: (effort: NewsDirectionEffort) => api.newsDirections(effort),
    onSuccess: (data, effort) => {
      client.setQueryData(QK.newsDirectionJob(data.id), data)
      setImmediateDirectionResult(data.status === 'running' ? null : data)
      if (data.status === 'running') {
        const next = { id: data.id, effort }
        setDirectionSession(next)
        setDirectionEffort(effort)
        saveNewsDirectionSession(next)
      } else {
        setDirectionSession({ id: '', effort })
        setDirectionEffort(effort)
        clearNewsDirectionSession()
        if (data.status === 'complete' || data.status === 'partial') {
          void client.invalidateQueries({ queryKey: QK.newsFeed })
        }
      }
    },
  })
  const directionResult = directionJob.data || immediateDirectionResult
  const directionRequestError = startDirection.error || directionJob.error
  const missingDirectionJob = directionJob.error instanceof ApiError && directionJob.error.status === 404
  useEffect(() => {
    if (!directionSession.id || !missingDirectionJob) return
    const id = directionSession.id
    clearNewsDirectionSession()
    client.removeQueries({ queryKey: QK.newsDirectionJob(id), exact: true })
    setImmediateDirectionResult({
      id, status: 'failed', total: directionJob.data?.total || 0, completed: directionJob.data?.completed || 0,
      error: '后台判断任务已不存在（服务可能重启），可以重新启动方向判断。',
      model: directionJob.data?.model || 'gpt-6-luna', reasoning_effort: directionSession.effort,
      cache_hit: directionJob.data?.cache_hit || false,
    })
    setDirectionSession(current => current.id === id ? { ...current, id: '' } : current)
  }, [client, directionSession.id, directionSession.effort, directionJob.data, missingDirectionJob])
  useEffect(() => {
    const result = directionJob.data
    if (!result) return
    if (result.status === 'running') {
      saveNewsDirectionSession({ id: result.id, effort: directionSession.effort })
      return
    }
    clearNewsDirectionSession()
    setImmediateDirectionResult(result)
    setDirectionSession(current => current.id ? { ...current, id: '' } : current)
    if ((result.status === 'complete' || result.status === 'partial') && refreshedDirectionJob.current !== result.id) {
      refreshedDirectionJob.current = result.id
      void client.invalidateQueries({ queryKey: QK.newsFeed })
    }
  }, [client, directionJob.data?.id, directionJob.data?.status, directionSession.effort])
  const analyze = useMutation({ mutationFn: (id: string) => api.newsAnalyze(id),
    onSuccess: data => {
      if (data.status === 'busy') { setError(data.error || '已有 AI 解读正在运行，请稍后重试'); return }
      client.setQueryData(QK.newsAnalysis(data.id), data); update({ report: data.id }); setError('')
    },
    onError: e => setError(e instanceof Error ? e.message : 'AI 解读未能启动') })
  const report = useQuery<NewsAnalysis>({ queryKey: QK.newsAnalysis(reportId), queryFn: () => api.newsAnalysis(reportId),
    enabled: !!reportId, staleTime: Infinity, retry: false, refetchOnWindowFocus: false,
    refetchInterval: query => query.state.data?.status === 'running' ? 2000 : false })
  const feedData = feed.data?.today === feedDay ? feed.data : undefined
  const rows = feedData?.items || []
  const relevant = new Set(feedData?.related_symbols || [])
  const base = useMemo(() => rows.filter(item => {
    if (source !== 'all' && !item.origins.some(origin => origin.source === source)) return false
    if (view === 'stocks' && item.associations.length === 0) return false
    if (scope === 'mine' && !item.associations.some(stock => relevant.has(stock.symbol))) return false
    const text = `${item.title} ${item.summary} ${item.associations.map(stock => `${stock.name} ${stock.symbol}`).join(' ')}`
    return !search.trim() || text.toLowerCase().includes(search.trim().toLowerCase())
  }), [rows, source, view, scope, search, feedData?.related_symbols])
  const visible = base.filter(item => category === 'all' || item.classifications[classification].category === category)
  const counts = Object.fromEntries(CATEGORIES[classification].map(([key]) => [key,
    base.filter(item => item.classifications[classification].category === key).length]))
  const waitingForAssociationSync = scope === 'mine' && feedData?.association_status === ''
  const busy = feed.isFetching || refresh.isPending
  const generate = (id: string) => { setError(''); analyze.mutate(id) }
  const directionStatusLabels: Record<NewsDirectionJob['status'], string> = {
    running: '正在后台判断', complete: '方向判断完成', partial: '部分快讯已完成判断',
    failed: '方向判断失败', busy: '已有方向判断任务正在运行', unconfigured: 'AI 尚未配置',
    unsupported: '当前 AI 配置不支持方向判断', empty: '当前没有可判断的当日快讯',
  }

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
          <span className="text-muted">{feedData?.today || '北京时间'} <strong className="ml-1 font-mono text-foreground">{feedData?.summary.today_count ?? '—'}</strong> 条去重样本</span>
          {feedData?.summary.themes.slice(0, 3).map(theme => <span key={theme.label} className={cn(badge, 'border-accent/25 bg-accent/5 text-accent')}>{theme.label} {theme.count}</span>)}
          <span className="text-muted">当前快照 {feedData?.summary.total_count ?? '—'} 条 · 非全市场历史档案</span>
          <button disabled={analyze.isPending || !feedData?.summary.today_count} onClick={() => generate('daily')}
            className="ml-auto inline-flex items-center gap-1.5 rounded border border-violet-400/30 px-2.5 py-1.5 text-violet-400 hover:bg-violet-400/10 disabled:opacity-40"><Sparkles size={14} />AI 今日研判</button>
        </section>
        <section aria-label="AI 当日方向判断" className="rounded-lg border border-border bg-surface px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="mr-auto min-w-56">
              <p className="text-sm font-semibold">AI 当日方向判断</p>
              <p className="mt-0.5 text-[11px] text-muted">只在点击后启动；处理当前快照最新 240 条当日快讯，不足 240 条时按实际数量，只复用已完成判断。关闭页面不会取消任务；服务中断时，尚未取得结果的模型调用仍可能消耗额度。</p>
            </div>
            <label className="flex items-center gap-2 text-xs text-muted">Luna 推理档位
              <select aria-label="AI方向推理档位" value={directionEffort} disabled={startDirection.isPending || !!directionSession.id || directionResult?.status === 'running'}
                onChange={event => setDirectionEffort(event.target.value as NewsDirectionEffort)}
                className="h-8 rounded border border-border bg-base px-2 text-foreground disabled:opacity-50">
                <option value="high">high（默认）</option><option value="max">max</option>
              </select>
            </label>
            <button disabled={!feedData?.summary.today_count || startDirection.isPending || !!directionSession.id || directionResult?.status === 'running'}
              onClick={() => startDirection.mutate(directionEffort)}
              className="inline-flex items-center gap-1.5 rounded border border-violet-400/30 px-3 py-1.5 text-xs text-violet-400 hover:bg-violet-400/10 disabled:opacity-40">
              {startDirection.isPending ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
              {startDirection.isPending ? '正在提交…' : directionSession.id || directionResult?.status === 'running' ? '判断进行中' : 'AI 判断当日方向'}
            </button>
          </div>
          {directionSession.id && !directionResult && directionJob.isFetching && <p className="mt-2 text-xs text-muted">正在接回已启动的后台判断任务…</p>}
          {directionSession.id && !directionResult && directionJob.error && <p role="status" className="mt-2 text-xs text-muted">后台判断任务状态暂不可用，将继续核对且不会重复提交。</p>}
          {directionResult && <div role="status" className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <span className={directionResult.status === 'failed' ? 'text-amber-400' : 'text-violet-400'}>{directionStatusLabels[directionResult.status]}</span>
            <span>{directionResult.completed}/{directionResult.total} 条</span>
            <span>{directionResult.model} · {directionResult.reasoning_effort}</span>
            {directionResult.cache_hit && <span>复用已有缓存</span>}
            {directionResult.error && <span className="text-amber-400">{directionResult.error}</span>}
            {directionResult.status === 'unconfigured' && <Link to="/settings?tab=ai" className="text-accent underline">去配置 AI</Link>}
          </div>}
          {directionRequestError && <p role="alert" className="mt-2 text-xs text-amber-400">{directionRequestError.message || 'AI 方向判断暂不可用'}</p>}
        </section>
        {feedData?.cache_snapshot && <div role="status" className="rounded border border-amber-400/25 bg-amber-400/5 px-3 py-2 text-xs text-amber-300">
          缓存快照，个股持仓关联待更新 · 保留最新 {feedData.cache_snapshot.retained_items}/{feedData.cache_snapshot.total_items} 条快讯
          {feedData.cache_snapshot.truncated_summaries > 0 && ` · ${feedData.cache_snapshot.truncated_summaries} 条摘要已明确截断`}
          {feedData.cache_snapshot.truncated_titles > 0 && ` · ${feedData.cache_snapshot.truncated_titles} 条标题已明确截断`}
        </div>}
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[11px] text-muted" aria-label="快讯来源状态">
          {feedData?.sources.map(item => <span key={item.source} title={item.reason || ''} className={cn('inline-flex items-center gap-1.5', item.status === 'stale' || item.status === 'unavailable' ? 'text-amber-400' : '')}>
            <span className={cn('h-1.5 w-1.5 rounded-full', item.status === 'ok' ? 'bg-emerald-400' : 'bg-amber-400')} />
            {item.label} {sourceStatusLabel(item, feedData.refreshing)} · {item.count} 条 · 拉取 {timeText(item.fetched_at)}
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
          {feedData?.association_status && feedData.association_status !== 'ok' && <span className="text-xs text-amber-400">{feedData.association_status}</span>}
        </div>
        <div className="flex flex-wrap gap-1.5" aria-label="快讯分类">
          {[['all', '全部'], ...CATEGORIES[classification]].map(([key, label]) => <button key={key} aria-pressed={category === key} onClick={() => update({ category: key })}
            className={cn('rounded border px-2 py-1 text-xs', category === key ? 'border-accent/60 bg-accent/10 text-accent' : 'border-border text-muted hover:text-foreground')}>{label} <span className="ml-1 font-mono">{key === 'all' ? base.length : counts[key] || 0}</span></button>)}
        </div>
        {(error || feed.error) && <div role="alert" className="rounded border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400">{error || (feed.error instanceof Error ? feed.error.message : '快讯加载失败')}</div>}
        {feed.isLoading || (feed.isFetching && !feedData) ? <div className="flex items-center justify-center gap-2 py-20 text-sm text-muted"><Loader2 className="animate-spin" size={18} />正在拉取公开快讯…</div>
          : visible.length === 0 ? <div className="rounded-lg border border-border py-20 text-center text-sm text-muted">{waitingForAssociationSync ? '快讯缓存已恢复，个股持仓关联待更新' : rows.length === 0 ? '暂无有效快讯，请核对来源状态后刷新' : '没有符合当前筛选的快讯'}{scope === 'mine' && !waitingForAssociationSync && <p className="mt-2 text-xs">仅匹配本地自选和登记的股票 / ETF，未命中不代表没有相关新闻。</p>}</div>
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
  const aiDirection = item.ai_direction
  return <article className="border-b border-border/70 px-4 py-3.5 last:border-b-0 sm:px-5">
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
      {item.origins.map((origin, index) => <span key={`${origin.source}-${index}`} title={`该来源发布于 ${timeText(origin.published_at)}`} className={cn(badge, origin.source === 'cls' ? 'border-red-400/30 text-red-400' : 'border-amber-400/30 text-amber-400')}>{origin.label}</span>)}
      <span className={cn(badge, cls.category === 'risk' ? 'border-red-400/30 text-red-400' : cls.category === 'other' ? 'border-border' : 'border-cyan-400/25 text-cyan-400')}>{cls.label}</span>
      <span title="词表相关度，不是 AI 置信度、胜率或收益" className="font-mono">{cls.score}</span><time dateTime={item.published_at} className="font-mono">{timeText(item.published_at)}</time>
      <span className={cn(badge, aiDirection?.direction === 'positive' ? 'border-emerald-400/25 text-emerald-400' : aiDirection?.direction === 'negative' ? 'border-red-400/25 text-red-400' : 'border-border')}
        title={aiDirection ? aiDirection.reason : `本标签来自本地词表，不是 AI 方向判断。命中词：${item.sentiment.matched.join('、')}`}>
        {aiDirection ? AI_DIRECTIONS[aiDirection.direction] : `${DIRECTIONS[item.sentiment.direction]} · 词表`}
      </span>
      <button onClick={onAnalyze} disabled={analyzing} className="ml-auto inline-flex items-center gap-1 text-muted hover:text-accent disabled:opacity-40"><Sparkles size={14} />解读</button>
    </div>
    <h3 className="mt-2 text-sm font-semibold leading-relaxed sm:text-[15px]">{item.title}</h3>
    {item.summary && item.summary !== item.title && <p className="mt-1 line-clamp-3 text-xs leading-6 text-muted">{item.summary}</p>}
    {aiDirection && <details className="mt-2 rounded border border-violet-400/20 bg-violet-400/5 px-3 py-2 text-xs">
      <summary className="cursor-pointer text-violet-300">AI 判断依据与范围</summary>
      <p className="mt-2 leading-relaxed text-foreground">{aiDirection.reason}</p>
      {aiDirection.evidence.length > 0 && <ul className="mt-1 list-inside list-disc space-y-0.5 text-muted">
        {aiDirection.evidence.map((evidence, index) => <li key={`${index}-${evidence}`}>{evidence}</li>)}
      </ul>}
      <p className="mt-2 text-[11px] text-muted">{AI_SCOPES[aiDirection.scope]} · {aiDirection.model} · {aiDirection.reasoning_effort} · 判断于 {timeText(aiDirection.generated_at)}</p>
    </details>}
    <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
      {cls.matched.length > 0 && <span className="mr-2">命中：{cls.matched.join('、')}</span>}
      {item.associations.length > 0 && <span>关联：</span>}
      {item.associations.map(stock => <button key={stock.symbol} title={stock.basis} onClick={() => onPreview(stock)} className="rounded border border-cyan-400/20 bg-cyan-400/5 px-1.5 py-0.5 text-cyan-400 hover:bg-cyan-400/15">{stock.asset_type === 'etf' && 'ETF · '}{stock.name} <span className="font-mono">{stock.symbol}</span>{!stock.direct && ' · 推断'}</button>)}
      {item.origins.filter(origin => origin.url).map((origin, index) => <a key={`${origin.source}-link-${index}`} href={origin.url!} target="_blank" rel="noopener noreferrer" className="ml-2 inline-flex items-center gap-1 hover:text-foreground">{origin.label}原文<ExternalLink size={11} /></a>)}
    </div>
  </article>
}
