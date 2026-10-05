import { useState, type Ref } from 'react'
import { ArrowUpRight, Check, Copy, LineChart, Loader2, Plus, Sparkles } from 'lucide-react'
import { api } from '@/lib/api'
import { formatMoney, formatPct, type Candidate } from './client'
import { candidateMissing, candidatePriceLabel, numberText, technicalSummary } from './candidateDisplay'
import { reportExcerpt, type CandidateReport } from './useCandidateResearch'

export function StockCandidateCard({ item, assetType, asOf, provider, quoteTime, selected, rank, report, analysisError, running, busy, configured, cardRef, onSelect, onAnalyze, onPreview }: {
  item: Candidate; assetType: 'stock' | 'etf'; asOf: string | null; provider: string | null; quoteTime: number | null
  selected: boolean; rank: number; report?: CandidateReport; analysisError?: string; running: boolean; busy: boolean; configured: boolean; cardRef?: Ref<HTMLElement>
  onSelect: () => void; onAnalyze: () => void; onPreview: () => void
}) {
  const [copied, setCopied] = useState(false)
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState(false)
  const [actionError, setActionError] = useState('')
  const missing = candidateMissing(item, assetType)
  const metrics = item.metrics
  async function copyCode() {
    try { await navigator.clipboard.writeText(item.symbol); setCopied(true); setActionError('') }
    catch { setActionError('复制失败，请手动复制代码') }
  }
  async function addWatchlist() {
    setAdding(true)
    try { await api.watchlistAdd(item.symbol); setAdded(true); setActionError('') }
    catch (error) { setActionError(error instanceof Error ? error.message : '加入自选失败') }
    finally { setAdding(false) }
  }
  const aiLabel = running ? 'AI 分析中' : report?.complete ? 'AI 已分析' : report?.error ? '分析未完成' : '待 AI 分析'
  return <article ref={cardRef} className={`flex min-w-0 flex-col overflow-hidden rounded-lg border text-xs leading-[1.6] transition-colors ${selected ? 'border-accent/60 bg-accent/[0.04]' : 'border-border bg-background/40 hover:border-accent/30'}`}>
    <button aria-label={`查看 ${item.name} 候选详情`} aria-pressed={selected} onClick={onSelect} className="flex w-full items-start justify-between gap-2 border-b border-border/70 p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent">
      <div className="min-w-0"><div className="flex flex-wrap items-center gap-x-2 gap-y-1"><span className="font-mono text-[11px] font-medium text-accent">{item.symbol}</span><h3 className="text-sm font-semibold text-foreground">{item.name}</h3></div><div className="mt-1 flex flex-wrap gap-1"><span className="rounded bg-accent/10 px-1.5 text-[11px] text-accent">{assetType === 'etf' ? '场内 ETF' : 'A 股'}</span><span className="rounded border border-border px-1.5 text-[11px] text-secondary">排名 #{rank}</span><span className="rounded border border-warning/25 bg-warning/5 px-1.5 text-[11px] text-warning">命中 {item.hit_count} 条策略</span></div></div>
      <div className="shrink-0 text-right"><div className="font-mono text-base font-semibold text-foreground">{numberText(metrics.close)}</div><div className={`font-mono text-xs ${metrics.change_pct == null ? 'text-muted' : metrics.change_pct >= 0 ? 'text-bull' : 'text-bear'}`}>{formatPct(metrics.change_pct)}</div><div className="mt-0.5 text-[10px] text-muted">{candidatePriceLabel(item)}</div></div>
    </button>
    <div className="flex-1 space-y-2.5 p-3">
      <div className="flex flex-wrap gap-1 text-[11px]"><span className="rounded border border-border px-1.5 text-secondary">{item.price_source === 'live' ? `${provider || '行情源'} · ${quoteTime ? new Date(quoteTime).toLocaleTimeString() : '时间未提供'}` : `策略快照 ${asOf || '未提供'}`}</span><span className={`rounded border px-1.5 ${report?.complete ? 'border-accent/30 bg-accent/10 text-accent' : 'border-border text-muted'}`}>{aiLabel}</span><span className="rounded border border-border px-1.5 text-muted">研究观察</span></div>
      {!!missing.length && <p className="text-[11px] text-warning">待补齐：{missing.join('、')}；当前不生成执行指令</p>}
      <div className="grid grid-cols-3 gap-x-2 gap-y-2 rounded-md border border-border/70 bg-surface/60 p-2">
        {[['换手率', metrics.turnover_rate == null ? '—' : `${numberText(metrics.turnover_rate)}%`], ['日线量比', numberText(metrics.vol_ratio_5d)], ['成交额', formatMoney(metrics.amount)], ...(assetType === 'stock' ? [['PE(TTM)', numberText(metrics.pe_ttm)], ['PB', numberText(metrics.pb)]] : [['资产类型', '场内 ETF'], ['折溢价', '未提供']]), ['MA20', numberText(metrics.ma20)]].map(([label, value]) => <div key={label}><div className="text-[11px] text-muted">{label}</div><div className="font-mono text-xs text-foreground">{value}</div></div>)}
      </div>
      <p className="text-[10px] text-muted">价格按上方口径；量比、估值与技术指标来自日线快照</p>
      <div><div className="flex items-baseline justify-between gap-2"><span className="text-[11px] text-muted">技术依据</span><span className="text-[10px] text-muted">前复权日线 · {item.technical_as_of || asOf || '日期未知'}</span></div><p className="mt-0.5 text-[11px] text-warning">{technicalSummary(metrics)}</p><p className="mt-0.5 font-mono text-[11px] text-secondary">MACD {numberText(metrics.macd_dif, 3)} / {numberText(metrics.macd_dea, 3)} · K/D {numberText(metrics.kdj_k, 1)} / {numberText(metrics.kdj_d, 1)}</p></div>
      <div><div className="mb-1 text-[11px] text-muted">策略命中 · 非 AI 评分</div><div className="flex flex-wrap gap-1">{item.strategies.slice(0, 4).map(strategy => <span key={strategy.id} title={strategy.description} className="max-w-full break-words rounded border border-accent/20 bg-accent/10 px-1.5 py-0.5 text-[11px] text-accent">{strategy.name}</span>)}{item.strategies.length > 4 && <span className="text-[11px] text-muted">+{item.strategies.length - 4}</span>}</div><p className="mt-1 line-clamp-2 text-[11px] text-secondary">{item.strategies[0]?.description || '请在详情查看完整策略条件。'}</p></div>
      <div className="rounded-md border border-border/70 bg-surface/50 px-2.5 py-2"><div className="mb-1 flex items-center gap-1 text-[11px] font-medium text-accent"><Sparkles className="h-3 w-3" />AI 研究摘要</div><p className="line-clamp-3 text-xs text-secondary">{running ? '正在调用模型，完成后保留摘要与完整报告…' : report?.content ? `${report.complete ? '' : '未完成：'}${reportExcerpt(report.content)}` : '点击 AI 分析，结合技术、基本面与数据缺口，生成观察、条件买入和退出计划。'}</p>{report?.content && <div className="mt-1 text-[10px] text-muted">AI 依据截至 {report.asOf || '未提供'} · {new Date(report.generatedAt).toLocaleString()}</div>}{analysisError && <p className="mt-1 text-[11px] text-danger">{analysisError}</p>}</div>
    </div>
    <div className="border-t border-border/70 px-2 py-1.5"><div className="flex flex-wrap items-center justify-between gap-1 text-[11px]">
      <button className="inline-flex min-h-11 sm:min-h-9 items-center gap-1 rounded px-1.5 text-accent hover:bg-accent/10" onClick={onPreview}><LineChart className="h-3 w-3" />K 线</button>
      <button disabled={adding || added} onClick={addWatchlist} className="inline-flex min-h-11 sm:min-h-9 items-center gap-1 rounded px-1.5 text-secondary hover:bg-elevated disabled:opacity-60">{adding ? <Loader2 className="h-3 w-3 animate-spin" /> : added ? <Check className="h-3 w-3" /> : <Plus className="h-3 w-3" />}{added ? '已自选' : '自选'}</button>
      <button onClick={copyCode} className="inline-flex min-h-11 sm:min-h-9 items-center gap-1 rounded px-1.5 text-secondary hover:bg-elevated"><Copy className="h-3 w-3" />{copied ? '已复制' : '代码'}</button>
      <button onClick={report?.complete ? onSelect : onAnalyze} disabled={running || (busy && !report?.complete) || (!configured && !report?.complete)} className="inline-flex min-h-11 sm:min-h-9 items-center gap-1 rounded bg-accent/10 px-2 text-accent hover:bg-accent/20 disabled:opacity-50">{running ? <Loader2 className="h-3 w-3 animate-spin" /> : <Sparkles className="h-3 w-3" />}{running ? '分析中' : report?.complete ? '查看分析' : 'AI 分析'}<ArrowUpRight className="h-3 w-3" /></button>
    </div>{actionError && <p role="alert" className="px-1.5 text-[11px] text-danger">{actionError}</p>}</div>
  </article>
}
