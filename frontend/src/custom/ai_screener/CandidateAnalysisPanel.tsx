import { Loader2, Sparkles, Square } from 'lucide-react'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { type Candidate } from './client'
import { researchKey, type CandidateResearch } from './useCandidateResearch'

export function CandidateAnalysisPanel({ candidate, assetType, configured, model, research }: {
  candidate: Candidate; assetType: 'stock' | 'etf'; configured: boolean; model?: string; research: CandidateResearch
}) {
  const key = researchKey(assetType, candidate.symbol)
  const completed = research.reports[key]
  const draft = research.drafts[key]
  const running = research.runningKey
  const report = running === key ? draft : completed ?? draft
  return <section className="mt-4 border-t border-border pt-4" aria-busy={running === key}>
    <div className="flex items-center justify-between gap-2">
      <h3 className="flex items-center gap-1.5 text-xs font-semibold text-foreground"><Sparkles className="h-3.5 w-3.5 text-accent" />AI 分析与条件计划</h3>
      {running === key ? <button onClick={research.cancel} className="inline-flex items-center gap-1 rounded-btn border border-border px-2 py-1 text-[11px] text-secondary"><Square className="h-3 w-3" />停止</button>
        : <button onClick={() => research.generate(candidate, assetType)} disabled={!!running || !configured} className="rounded-btn border border-accent/40 bg-accent/10 px-2.5 py-1 text-[11px] text-accent disabled:opacity-50">{report?.content ? '重新分析' : '生成分析'}</button>}
    </div>
    <p className="mt-2 text-[11px] leading-4 text-muted">{model || '已配置模型'} · 根据日线、关键价位和数据缺口分析观察、分批与退出条件。历史复权价位需核对最新不复权报价，不会自动下单。</p>
    {!configured && <p className="mt-2 text-xs text-warning">先在设置中配置 AI 模型。</p>}
    {running && <p role="status" className="mt-2 flex items-center gap-1.5 text-[11px] text-secondary"><Loader2 className="h-3 w-3 animate-spin" />{running === key ? '模型正在分析，完成后显示计划…' : '另一个候选正在分析，可继续查看资料。'}</p>}
    {draft?.error && <p role="alert" className="mt-2 text-xs text-danger">{draft.error}{completed && ' 下方保留上次完整报告。'}</p>}
    {report?.content ? <>
      <div className="mb-2 mt-3 text-[11px] text-muted">{report.complete ? '完整研究快照' : '未完成研究'} · 日线截至 {report.asOf || '未提供'} · 生成 {new Date(report.generatedAt).toLocaleString()}</div>
      <div className="min-w-0 overflow-x-auto text-xs"><MarkdownRenderer content={report.content} /></div>
    </> : !running && <p className="mt-3 rounded-md border border-dashed border-border p-3 text-xs leading-5 text-muted">点击卡片“AI 分析”或这里的“生成分析”，查看该{assetType === 'etf' ? 'ETF' : '股票'}的条件研究计划。策略命中本身不会触发买入。</p>}
  </section>
}
