import { useEffect, useRef, useState } from 'react'
import { Loader2, Sparkles, Square } from 'lucide-react'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { analyzeCandidate, type Candidate } from './client'

type Report = { content: string; asOf: string | null; generatedAt: number; error: string; complete: boolean }
const STORAGE_KEY = 'stockfund.candidate-research.v1'
const MAX_REPORTS = 24

function loadReports(): Record<string, Report> {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}')
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
    return Object.fromEntries(Object.entries(raw).filter(([, r]) => r && typeof r.content === 'string'
      && r.content.length <= 30000 && r.content.trim() && r.complete === true && r.error === '' && Number.isFinite(r.generatedAt)
      && (r.asOf === null || typeof r.asOf === 'string')).slice(-MAX_REPORTS))
  } catch { return {} }
}

export function CandidateAnalysisPanel({ candidate, assetType, configured }: {
  candidate: Candidate; assetType: 'stock' | 'etf'; configured: boolean
}) {
  const [reports, setReports] = useState(loadReports)
  const [running, setRunning] = useState<string | null>(null)
  const controller = useRef<AbortController | null>(null)
  const key = `${assetType}:${candidate.symbol}`
  const report = reports[key]
  useEffect(() => () => controller.current?.abort(), [])

  async function generate() {
    if (controller.current) return
    const abort = new AbortController()
    controller.current = abort
    setRunning(key)
    let content = ''
    let asOf: string | null = null
    let done = false
    const update = (error = '') => setReports(prev => ({ ...prev, [key]: { content, asOf, generatedAt: Date.now(), error, complete: false } }))
    update()
    try {
      for await (const event of analyzeCandidate(candidate.symbol, abort.signal)) {
        if (event.type === 'meta') asOf = event.as_of ?? null
        if (event.type === 'delta') {
          content += event.content || ''
          if (content.length > 30000) throw new Error('分析内容过长，停止接收；请缩小范围')
          update()
        }
        if (event.type === 'error') throw new Error(event.message || '分析失败')
        if (event.type === 'done') done = true
      }
      if (!done || !content.trim()) throw new Error('分析未完整返回，请重试')
      setReports(prev => {
        const next = Object.fromEntries(Object.entries({ ...prev, [key]: { content, asOf, generatedAt: Date.now(), error: '', complete: true } })
          .sort((a, b) => a[1].generatedAt - b[1].generatedAt).slice(-MAX_REPORTS))
        try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(Object.entries(next).filter(([, r]) => r.complete)))) } catch { /* 当前标签页内存仍保留 */ }
        return next
      })
    } catch (error) {
      update(abort.signal.aborted ? '已停止接收，未完成内容仅供核对；模型任务可能稍后退出。' : error instanceof Error ? error.message : '分析失败')
    } finally {
      controller.current = null
      setRunning(null)
    }
  }

  return <section className="mt-4 border-t border-border pt-4" aria-busy={running === key}>
    <div className="flex items-center justify-between gap-2">
      <h3 className="flex items-center gap-1.5 text-xs font-semibold text-foreground"><Sparkles className="h-3.5 w-3.5 text-accent" />AI 分析与买入计划</h3>
      {running === key ? <button onClick={() => controller.current?.abort()} className="inline-flex items-center gap-1 rounded-btn border border-border px-2 py-1 text-[11px] text-secondary"><Square className="h-3 w-3" />停止</button>
        : <button onClick={generate} disabled={!!running || !configured} className="rounded-btn border border-accent/40 bg-accent/10 px-2.5 py-1 text-[11px] text-accent disabled:opacity-50">{report?.content ? '重新分析' : '生成分析'}</button>}
    </div>
    <p className="mt-2 text-[10px] leading-4 text-muted">基于本地日线与实际关键价位，生成观察、分批条件和退出条件；不会下单。历史复权价位需核对最新不复权报价。</p>
    {!configured && <p className="mt-2 text-xs text-warning">先在设置中配置 AI 模型。</p>}
    {running && <p role="status" className="mt-2 flex items-center gap-1.5 text-[11px] text-secondary"><Loader2 className="h-3 w-3 animate-spin" />{running === key ? '模型正在分析，完成后显示计划…' : '另一个候选正在分析，可继续查看资料。'}</p>}
    {report?.error && <p role="alert" className="mt-2 text-xs text-danger">{report.error}</p>}
    {report?.content ? <>
      <div className="mb-2 mt-3 text-[10px] text-muted">研究快照 · 日线截至 {report.asOf || '未提供'} · 生成 {new Date(report.generatedAt).toLocaleString()}</div>
      <div className="min-w-0 overflow-x-auto text-xs"><MarkdownRenderer content={report.content} /></div>
    </> : !running && <p className="mt-3 rounded-md border border-dashed border-border p-3 text-[11px] leading-5 text-muted">点击“生成分析”，查看该{assetType === 'etf' ? 'ETF' : '股票'}的条件研究计划。策略命中本身不会触发买入。</p>}
  </section>
}
