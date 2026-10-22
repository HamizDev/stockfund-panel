import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { BookOpen, ChevronLeft, ChevronRight, Code2, Copy, ExternalLink, Loader2, Search, X } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePageState } from '@/lib/usePageState'
import { INSTRUMENT_VIEW_GC_MS, useInstrumentViewCache } from '@/lib/useInstrumentViewCache'
import { PageHeader } from '@/components/PageHeader'
import type { PublicScript, ScriptKind } from './types'

type Mode = 'recipes' | 'online' | 'offline'
interface View {
  mode: Mode
  input: string
  author: string
  kind: ScriptKind
  search: { q: string; author: string; kind: ScriptKind; page: number } | null
  selected: { id: string; name: string; version: string } | null
}
const INITIAL: View = { mode: 'recipes', input: '', author: '', kind: 'all', search: null, selected: null }
const KINDS = ['all', 'indicator', 'strategy', 'library'] as const
const LABELS = { all: '全部类型', indicator: '指标', strategy: '策略', library: '函数库' }
const BUTTON = 'inline-flex min-h-9 items-center justify-center gap-1.5 rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:border-accent/50 hover:text-foreground disabled:opacity-40'
const INPUT = 'min-h-9 w-full rounded-btn border border-border bg-base px-3 py-1.5 text-xs text-foreground outline-none focus:border-accent'

function validView(value: unknown): value is View {
  if (!value || typeof value !== 'object') return false
  const v = value as View
  const short = (text: unknown, max: number) => typeof text === 'string' && text.length <= max
  return ['recipes', 'online', 'offline'].includes(v.mode) && short(v.input, 80) && short(v.author, 60)
    && KINDS.includes(v.kind) && (v.search === null || (!!v.search && short(v.search.q, 80)
      && short(v.search.author, 60) && KINDS.includes(v.search.kind) && Number.isInteger(v.search.page)
      && v.search.page >= 1 && v.search.page <= 20))
    && (v.selected === null || (!!v.selected && short(v.selected.name, 160)
      && /^PUB;[A-Za-z0-9]{1,100}$/.test(v.selected.id) && /^(last|\d{1,6}(\.\d{1,6})?)$/.test(v.selected.version)))
}

function safeScriptUrl(value: string | null) {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && url.host === 'www.tradingview.com'
      && /^\/script\/[A-Za-z0-9-]+\/?$/.test(url.pathname) && !url.search && !url.hash ? url.href : null
  } catch { return null }
}

function when(value: number | null) {
  return value == null ? '' : new Date(value).toLocaleString('zh-CN', { hour12: false })
}

export function IndicatorLibraryPanel() {
  const [view, setView] = usePageState<View>('indicatorLibrary', INITIAL, validView)
  const [copied, setCopied] = useState('')
  const sourceRef = useRef<HTMLElement>(null)
  const offline = view.mode === 'offline'
  const params = view.search ?? { q: '', author: '', kind: 'all' as ScriptKind, page: 1 }
  const searchKey = QK.indicatorSearch(params.q, params.author, params.kind, params.page, offline)
  const catalog = useQuery({ queryKey: QK.indicatorCatalog, queryFn: ({ signal }) => api.indicatorCatalog(signal), staleTime: 3600_000, gcTime: INSTRUMENT_VIEW_GC_MS })
  const results = useQuery({
    queryKey: searchKey,
    queryFn: ({ signal }) => api.indicatorSearch({ ...params, offline }, signal),
    enabled: view.mode === 'offline' || (view.mode === 'online' && view.search !== null),
    staleTime: 6 * 3600_000,
    gcTime: INSTRUMENT_VIEW_GC_MS,
    retry: false,
    meta: { instrumentView: 'indicator-search', symbol: JSON.stringify(searchKey) },
  })
  const selectedKey = view.selected ? `${view.selected.id}:${view.selected.version}:${offline}` : ''
  const source = useQuery({
    queryKey: QK.indicatorSource(view.selected?.id ?? '', view.selected?.version ?? '', offline),
    queryFn: ({ signal }) => api.indicatorSource(view.selected!.id, view.selected!.version, offline, signal),
    enabled: view.selected !== null && view.mode !== 'recipes',
    staleTime: 6 * 3600_000,
    gcTime: INSTRUMENT_VIEW_GC_MS,
    retry: false,
    meta: { instrumentView: 'indicator-source', symbol: selectedKey },
  })
  useInstrumentViewCache('indicator-source', selectedKey)
  useInstrumentViewCache('indicator-search', JSON.stringify(searchKey))
  useEffect(() => {
    if (selectedKey) sourceRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })
  }, [selectedKey])

  function mode(next: Mode) {
    setCopied('')
    setView(current => ({ ...current, mode: next, selected: null,
      search: next === 'offline' ? { q: current.input.trim(), author: current.author.trim(), kind: current.kind, page: 1 } : null }))
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    setView(current => ({ ...current, selected: null,
      search: { q: current.input.trim(), author: current.author.trim(), kind: current.kind, page: 1 } }))
  }
  function chooseTopic(topic: string) {
    setView(current => ({ ...current, input: topic, selected: null,
      search: { q: topic, author: current.author.trim(), kind: current.kind, page: 1 } }))
  }
  function select(item: PublicScript) {
    setCopied('')
    setView(current => ({ ...current, selected: { id: item.id, name: item.name,
      version: offline ? item.cached_version || item.version : item.version } }))
  }
  async function copy() {
    if (!source.data) return
    try { await navigator.clipboard.writeText(source.data.source); setCopied('已复制源码') }
    catch { setCopied('复制失败，请在源码区选择文字复制') }
  }

  return <section className="space-y-4" aria-label="指标研究库">
    <div className="flex flex-wrap gap-1 rounded-card border border-border bg-surface p-1" role="tablist" aria-label="指标研究范围">
      {([['recipes', '已有研究'], ['online', '在线发现'], ['offline', '离线检索']] as const).map(([id, label]) =>
        <button key={id} role="tab" aria-selected={view.mode === id} onClick={() => mode(id)}
          className={`min-h-9 rounded-btn px-4 text-xs ${view.mode === id ? 'bg-accent/15 text-accent' : 'text-secondary hover:bg-elevated'}`}>{label}</button>)}
    </div>
    {view.mode === 'recipes' ? <>
      <div className="flex flex-wrap items-center gap-2 text-xs text-secondary">
        <BookOpen className="h-4 w-4 text-accent" />
        <span>已接入研究回测的四套规则，先核对条件和成本，再比较结果。</span>
        <Link to="/crypto-paper" className="ml-auto text-accent hover:underline">打开数字资产研究回测 →</Link>
      </div>
      {catalog.isPending && <p role="status" className="text-xs text-muted">正在读取研究目录…</p>}
      {catalog.isError && <p role="alert" className="text-xs text-danger">研究目录读取失败，请重新打开或刷新。</p>}
      <div className="grid min-w-0 gap-3 lg:grid-cols-2">{catalog.data?.recipes.map(recipe =>
        <article key={recipe.id} className="min-w-0 rounded-card border border-border bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-medium text-foreground">{recipe.name}</h3><span className="rounded bg-accent/10 px-2 py-0.5 text-[10px] text-accent">{recipe.kind}</span></div>
          <p className="mt-2 text-xs leading-5 text-secondary">{recipe.description}</p>
          <dl className="mt-3 space-y-2 text-xs leading-5">
            {([['输入与参数', recipe.inputs], ['入场观察', recipe.entry], ['退出观察', recipe.exit]] as const).map(([label, text]) =>
              <div key={label}><dt className="text-muted">{label}</dt><dd className="text-secondary">{text}</dd></div>)}
          </dl>
          <p className="mt-3 border-t border-border pt-2 text-[11px] leading-5 text-warning">{recipe.caution}</p>
        </article>)}</div>
      <p className="text-[11px] leading-5 text-muted">这些是独立实现的研究规则，没有在本页执行 Pine，也不代表已有可信胜率。股票与 ETF 的交易规则需另行验证。</p>
    </> : <>
      <form onSubmit={submit} className="rounded-card border border-border bg-surface p-3 sm:p-4">
        <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,0.6fr)_auto_auto]">
          <label className="min-w-0 space-y-1"><span className="text-[11px] text-muted">{offline ? '名称、说明或源码关键词' : '主题（支持下方中文主题）'}</span>
            <input className={INPUT} aria-label="指标搜索关键词" maxLength={80} placeholder={offline ? '如 ta.rsi / strategy.entry' : '如 超级趋势 / 均值回归'} value={view.input} onChange={e => setView(v => ({ ...v, input: e.target.value }))} /></label>
          <label className="min-w-0 space-y-1"><span className="text-[11px] text-muted">作者账号（可选）</span>
            <input className={INPUT} aria-label="指标作者" maxLength={60} placeholder="TradingView 作者账号" value={view.author} onChange={e => setView(v => ({ ...v, author: e.target.value }))} /></label>
          <label className="space-y-1"><span className="text-[11px] text-muted">类型</span>
            <select aria-label="脚本类型" className={INPUT} value={view.kind} onChange={e => setView(v => ({ ...v, kind: e.target.value as ScriptKind }))}>
              {KINDS.map(kind => <option key={kind} value={kind}>{LABELS[kind]}</option>)}
            </select></label>
          <button className={`${BUTTON} self-end bg-accent/10 text-accent`} disabled={results.isFetching}><Search className="h-3.5 w-3.5" />{results.isFetching ? '查询中…' : '查询'}</button>
        </div>
        {!offline && <div className="mt-3 flex flex-wrap gap-1.5">{catalog.data?.topics.map(topic =>
          <button key={topic.id} type="button" disabled={results.isFetching} onClick={() => chooseTopic(topic.name)}
            className="rounded-btn border border-border px-2 py-1 text-[11px] text-secondary hover:border-accent/50 hover:text-accent disabled:opacity-40">{topic.name}</button>)}</div>}
        <p className="mt-3 text-[11px] leading-5 text-muted">{offline ? '只查本机已经发现的脚本及按需缓存的源码，不访问网络。' : '点击查询才联网；主题发现不等同于任意全文搜索。列表不下载源码，不需要交易密钥。'}</p>
      </form>
      {results.isFetching && <p role="status" className="flex items-center gap-2 text-xs text-secondary"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在查询{results.data ? '，仍显示已有结果' : ''}…</p>}
      {results.isError && <div role="alert" className="rounded-btn border border-danger/30 bg-danger/5 p-3 text-xs text-danger">{results.error.message}<button className="ml-3 underline" onClick={() => void results.refetch()}>重试</button></div>}
      {!offline && !view.search && <p className="py-8 text-center text-xs text-muted">选择主题或输入作者，开始发现公开指标。</p>}
      {results.data && <>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted"><span>{results.data.offline ? '离线库' : results.data.cached ? '查询缓存' : '本次查询'} · 第 {results.data.page} 页 · {results.data.items.length} 项</span><span>{when(results.data.fetched_at_ms)}</span></div>
        {results.data.items.length === 0 && <p className="rounded-card border border-border p-8 text-center text-xs text-muted">{offline ? '没有匹配的已缓存脚本。先在线发现，查看需要的源码后即可离线检索。' : '此页没有匹配的公开脚本，请换主题或作者。'}</p>}
        <div className="grid min-w-0 gap-3 lg:grid-cols-2">{results.data.items.map(item => {
          const url = safeScriptUrl(item.url)
          return <article key={item.id} className={`min-w-0 rounded-card border bg-surface p-3 sm:p-4 ${view.selected?.id === item.id ? 'border-accent/60' : 'border-border'}`}>
            <h3 className="break-words text-sm font-medium text-foreground">{item.name}</h3>
            <p className="mt-1 break-words text-[11px] text-muted">{item.author || '作者未标注'} · {LABELS[item.kind]} · 版本 {item.version}{item.source_cached ? ` · 已缓存源码 ${item.cached_version || ''}` : ''}</p>
            <p className="mt-2 break-words text-xs leading-5 text-secondary">{item.description || '暂无说明'}</p>
            <div className="mt-3 flex flex-wrap gap-2"><button className={BUTTON} onClick={() => select(item)} aria-expanded={view.selected?.id === item.id} disabled={offline && !item.source_cached}><Code2 className="h-3.5 w-3.5" />{offline ? '查看缓存源码' : '按需查看源码'}</button>
              {url && <a className={BUTTON} href={url} target="_blank" rel="noreferrer">原始脚本 <ExternalLink className="h-3 w-3" /></a>}</div>
          </article>
        })}</div>
        <div className="flex items-center justify-end gap-2 text-xs text-muted"><button className={BUTTON} disabled={params.page <= 1 || results.isFetching} onClick={() => setView(v => ({ ...v, selected: null, search: { ...params, page: params.page - 1 } }))}><ChevronLeft className="h-3 w-3" />上一页</button><span>{results.data.page}</span><button className={BUTTON} disabled={!results.data.has_more || results.isFetching} onClick={() => setView(v => ({ ...v, selected: null, search: { ...params, page: params.page + 1 } }))}>下一页<ChevronRight className="h-3 w-3" /></button></div>
      </>}
      {view.selected && <section ref={sourceRef} className="min-w-0 scroll-mt-4 rounded-card border border-accent/30 bg-surface p-3 sm:p-4" aria-label="脚本源码">
        <div className="flex items-start justify-between gap-2"><h3 className="min-w-0 break-words text-sm font-medium text-foreground">{view.selected.name}</h3><button className={BUTTON} aria-label="关闭源码" onClick={() => setView(v => ({ ...v, selected: null }))}><X className="h-3.5 w-3.5" /></button></div>
        {source.isPending && <p role="status" className="mt-3 text-xs text-secondary">正在按需读取公开源码…</p>}
        {source.isError && <p role="alert" className="mt-3 text-xs text-danger">{source.error.message}<button className="ml-3 underline" onClick={() => void source.refetch()}>重试</button></p>}
        {source.data && <>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted"><span>版本 {source.data.version} · Pine {source.data.pine_version ?? '未标注'} · {(source.data.bytes / 1024).toFixed(1)} KiB · {source.data.cached ? '缓存' : '本次读取'}</span><span>{when(source.data.fetched_at_ms)}</span><button className={`${BUTTON} ml-auto`} onClick={() => void copy()}><Copy className="h-3 w-3" />复制源码</button></div>
          {copied && <p role="status" className="mt-1 text-xs text-accent">{copied}</p>}
          <pre className="mt-3 max-h-[28rem] max-w-full overflow-auto rounded-btn border border-border bg-base p-3 text-[11px] leading-5 text-secondary" tabIndex={0}><code>{source.data.source}</code></pre>
          <p className="mt-2 text-[11px] leading-5 text-muted">源码按文本展示，保留作者声明；不会执行、生成订单或自动改写策略。换脚本时释放上一份前端源码，离开页面后暂存 30 分钟。</p>
        </>}
      </section>}
    </>}
  </section>
}

export function IndicatorLibraryPage() {
  return <div className="flex h-full min-h-0 flex-col bg-base">
    <PageHeader title="指标研究" className="pl-14 sm:pl-5" subtitle="规则说明 · 公开脚本 · 离线源码检索" right={<Link to="/data" className="text-xs text-accent hover:underline">返回数据工具</Link>} />
    <main className="min-h-0 flex-1 overflow-y-auto"><div className="mx-auto w-full max-w-6xl p-3 sm:p-5"><IndicatorLibraryPanel /></div></main>
  </div>
}
