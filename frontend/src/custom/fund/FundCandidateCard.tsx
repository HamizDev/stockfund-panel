import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, Copy, Loader2, Sparkles } from 'lucide-react'
import { toast } from '@/components/Toast'
import { copyText } from '@/lib/clipboard'
import type { AiFundType, FundRankItem, FundResearch } from './client'

export type FundAnalysisStatus = 'idle' | 'loading' | 'partial' | 'done' | 'error' | 'cancelled'
export type FundCandidateHorizon = '1m' | '3m' | '6m' | '1y' | '2y' | '3y'

export interface FundCandidateMetric {
  label: string
  value: string
  tone?: 'positive' | 'negative'
  title?: string
  wide?: boolean
}

const HORIZON_DETAILS: Record<FundCandidateHorizon, { label: string; alternate: FundCandidateHorizon }> = {
  '1m': { label: '近 1 月', alternate: '1y' },
  '3m': { label: '近 3 月', alternate: '1y' },
  '6m': { label: '近 6 月', alternate: '1y' },
  '1y': { label: '近 1 年', alternate: '3m' },
  '2y': { label: '近 2 年', alternate: '1y' },
  '3y': { label: '近 3 年', alternate: '1y' },
}

const HORIZON_LABELS: Record<FundCandidateHorizon, string> = {
  '1m': '近 1 月', '3m': '近 3 月', '6m': '近 6 月', '1y': '近 1 年', '2y': '近 2 年', '3y': '近 3 年',
}

const RESEARCH_FIELD_LABELS: Record<string, string> = {
  nav: '单位净值',
  nav_date: '净值日期',
  management_pct: '管理费',
  custody_pct: '托管费',
  sales_service_pct: '销售服务费',
  subscription_rules: '申购费规则',
  redemption_rules: '赎回费规则',
  max_drawdown_pct: '观测回撤',
  risk: '风险指标',
  fees: '费率',
  holdings: '定期报告持仓',
}

function growthFor(item: FundRankItem, horizon: FundCandidateHorizon): number | null {
  return item[`growth_${horizon}`]
}

function normalizeHorizon(value: string): FundCandidateHorizon {
  return Object.prototype.hasOwnProperty.call(HORIZON_DETAILS, value) ? value as FundCandidateHorizon : '1y'
}

function formatPercent(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '未提供' : `${value.toFixed(2)}%`
}

export function formatFundCandidateReturn(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '未提供' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

export function fundAnalysisStatusLabel(status?: FundAnalysisStatus): string {
  if (status === 'loading') return '进行中'
  if (status === 'partial') return '未完成'
  if (status === 'done') return '完成'
  if (status === 'error') return '失败'
  if (status === 'cancelled') return '已停止（当前未分析）'
  return '未分析'
}

export function summarizeFundAnalysisContent(content: string, maxCharacters = 180): string {
  const limit = Number.isFinite(maxCharacters) ? Math.max(1, Math.floor(maxCharacters)) : 180
  const summary = content
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s*>+\s?/gm, '')
    .replace(/^\s*(?:[-*+]|\d+\.)\s+/gm, '')
    .replace(/\|/g, ' ')
    .replace(/[*_~]/g, '')
    .replace(/\s+/g, ' ')
    .replace(/\s+([，。！？；：、,.!?])/g, '$1')
    .trim()
  return summary.length > limit ? `${summary.slice(0, limit).trimEnd()}…` : summary
}

export interface FundAnalysisSummaryPresentation {
  label: string
  text: string
  complete: boolean
}

export function fundAnalysisSummaryPresentation(
  status?: FundAnalysisStatus,
  content?: string,
  generatedAt?: number,
  error?: string,
): FundAnalysisSummaryPresentation {
  const hasContent = typeof content === 'string' && !!content.trim()
  const excerpt = summarizeFundAnalysisContent(content ?? '')
  const complete = status === 'done' && hasContent
  const generatedAtLabel = complete && generatedAt != null && Number.isFinite(generatedAt)
    ? ` · 完成于 ${new Date(generatedAt).toLocaleString()}`
    : ''
  const label = complete
    ? `AI 分析摘要${generatedAtLabel}：`
    : hasContent
      ? 'AI 分析摘要（未完成）：'
      : status === 'done'
        ? 'AI 分析摘要（未生成）：'
        : 'AI 分析摘要：'
  const text = excerpt
    || (hasContent
      ? complete
        ? '分析内容已返回，但未提取到可显示摘要；请在侧栏查看完整内容。'
        : '已收到未完成的分析片段；请在侧栏查看完整内容。'
      : status === 'loading'
        ? '正在等待 AI 分析内容。'
        : status === 'cancelled'
          ? '接收已停止，尚未收到 AI 分析内容。'
          : status === 'error'
            ? error || 'AI 分析失败，可重试或查看侧栏状态。'
            : status === 'partial'
              ? '分析未完成，尚未收到可显示的 AI 分析内容。'
              : '尚未生成 AI 摘要。')
  return { label, text, complete }
}

export function fundCandidateMetrics(item: FundRankItem, horizonValue: string): FundCandidateMetric[] {
  const horizon = normalizeHorizon(horizonValue)
  const research = item.research
  const detail = HORIZON_DETAILS[horizon]
  const selectedReturn = growthFor(item, horizon)
  const alternateReturn = growthFor(item, detail.alternate)
  const annualFees = research?.fees
    ? `管理 ${formatPercent(research.fees.management_pct)} · 托管 ${formatPercent(research.fees.custody_pct)} · 销售服务 ${formatPercent(research.fees.sales_service_pct)}`
    : research ? '未提供' : '待获取'
  const drawdown = research ? formatPercent(research.risk?.max_drawdown_pct) : '待获取'
  const observations = !research
    ? '待获取'
    : research.risk?.status === 'ok' && Number.isFinite(research.risk.observations)
      ? `${research.risk.observations.toLocaleString()} 个`
      : '未提供'

  return [
    {
      label: `${detail.label}收益`,
      value: formatFundCandidateReturn(selectedReturn),
      tone: selectedReturn == null ? undefined : selectedReturn >= 0 ? 'positive' : 'negative',
    },
    {
      label: `${HORIZON_LABELS[detail.alternate]}收益`,
      value: formatFundCandidateReturn(alternateReturn),
      tone: alternateReturn == null ? undefined : alternateReturn >= 0 ? 'positive' : 'negative',
    },
    {
      label: '观测最大回撤',
      value: drawdown,
      title: research?.risk?.note || undefined,
    },
    {
      label: '风险样本',
      value: observations,
      title: research?.risk?.note || undefined,
    },
    {
      label: '公开年费率',
      value: annualFees,
      title: annualFees,
      wide: true,
    },
    {
      label: '持仓报告期',
      value: research ? research.holdings?.report_date || '未提供' : '待获取',
      wide: true,
    },
  ]
}

export function fundCandidateBasis(
  item: FundRankItem,
  resultFundType: AiFundType,
  horizonValue: string,
): string {
  const horizon = normalizeHorizon(horizonValue)
  const category = item.fund_type ?? (resultFundType === 'all' ? null : resultFundType)
  const details = [
    category ? `来自${category}公开榜单` : '来自公开基金排名',
    item.share_class ? `${item.share_class} 类份额` : '份额类别未提供',
    `${HORIZON_LABELS[horizon]}收益 ${formatFundCandidateReturn(growthFor(item, horizon))}`,
  ]
  return details.join(' · ')
}

export function fundCandidateDataGap(research?: FundResearch): string {
  if (!research) return '研究资料待获取：回撤、费率和定期报告持仓。'
  const warning = Array.isArray(research.warnings) ? research.warnings.find((value) => value.trim()) : undefined
  const missingFields = Array.isArray(research.missing_fields) ? research.missing_fields : []
  const findings = new Set(missingFields.map((field) => RESEARCH_FIELD_LABELS[field] ?? field))
  if (research.risk?.status === 'unavailable') findings.add('风险指标')
  if (research.fees?.status === 'unavailable') findings.add('费率')
  if (research.fees?.status === 'partial') findings.add('费率部分可用')
  if (research.holdings?.status === 'unavailable') findings.add('定期报告持仓')
  const warningText = warning?.replace(/^(?:资料缺口|数据缺口|缺口提醒)\s*[：:]\s*/, '').trim()
  const findingText = Array.from(findings).slice(0, 3).join('、')
  const messages = [warningText, findingText ? `缺少或不完整：${findingText}` : ''].filter(Boolean)
  return messages.join('；') || '未报告额外资料缺口；持仓信息仅指定期报告披露。'
}

interface FundCandidateCardProps {
  item: FundRankItem
  selected: boolean
  rank?: number
  rankLabel?: string
  resultFundType: AiFundType
  horizon: string
  analysisStatus?: FundAnalysisStatus
  researchSummary?: string
  analysisContent?: string
  analysisGeneratedAt?: number
  analysisError?: string
  modelConfigured?: boolean
  modelProvider?: string
  selectedRef?: (element: HTMLElement | null) => void
  onSelect: (code: string) => void
  onAnalyze: (code: string) => void
  onViewAnalysis: (code: string) => void
}

export function FundCandidateCard({
  item,
  selected,
  rank,
  rankLabel,
  resultFundType,
  horizon,
  analysisStatus,
  researchSummary,
  analysisContent,
  analysisGeneratedAt,
  analysisError,
  modelConfigured,
  modelProvider,
  selectedRef,
  onSelect,
  onAnalyze,
  onViewAnalysis,
}: FundCandidateCardProps) {
  const [copyFeedback, setCopyFeedback] = useState<'copied' | 'error' | null>(null)
  const candidateHorizon = normalizeHorizon(horizon)
  const analysisLabel = fundAnalysisStatusLabel(analysisStatus)
  const analysisTone = analysisStatus === 'done'
    ? 'border-bull/25 bg-bull/10 text-bull'
    : analysisStatus === 'error'
      ? 'border-danger/25 bg-danger/10 text-danger'
      : analysisStatus === 'loading'
        ? 'border-accent/25 bg-accent/10 text-accent'
        : analysisStatus === 'partial'
          ? 'border-warning/25 bg-warning/10 text-warning'
        : 'border-border bg-base/60 text-muted'
  const navDate = item.nav_date ?? item.research?.nav_date
  const navDateLabel = navDate || (item.research ? '未提供' : '待获取')
  const growth = growthFor(item, candidateHorizon)
  const metrics = fundCandidateMetrics(item, candidateHorizon)
  const category = item.fund_type ?? (resultFundType === 'all' ? null : resultFundType)
  const holdingItems = item.research?.holdings?.items ?? []
  const gap = fundCandidateDataGap(item.research)
  const source = item.research?.source === 'eastmoney' ? '东方财富公开研究资料' : '东方财富公开排名'
  const copyCode = `${item.code}.OF`
  const detailsUrl = `/fund?code=${encodeURIComponent(copyCode)}&name=${encodeURIComponent(item.name)}&research_horizon=${encodeURIComponent(candidateHorizon)}`
  const actionLabel = analysisStatus === 'done'
    ? '查看分析'
    : analysisStatus === 'loading'
      ? '查看进度'
      : analysisStatus === 'error' || analysisStatus === 'partial'
        ? '重试分析'
        : 'AI分析'
  const researchSummaryText = typeof researchSummary === 'string' ? researchSummary.trim() : ''
  const hasAnalysisContent = typeof analysisContent === 'string' && !!analysisContent.trim()
  const analysisPresentation = fundAnalysisSummaryPresentation(analysisStatus, analysisContent, analysisGeneratedAt, analysisError)
  const modelLabel = modelConfigured === false
    ? 'AI 模型未配置'
    : modelProvider
      ? `AI 服务 ${modelProvider}`
      : modelConfigured === true
        ? 'AI 模型已配置'
        : 'AI 模型状态读取中'
  async function handleCopyCode() {
    setCopyFeedback(null)
    const copied = await copyText(copyCode)
    if (copied) {
      setCopyFeedback('copied')
      toast(`已复制基金代码 ${copyCode}`, 'success')
    } else {
      setCopyFeedback('error')
      toast('复制失败，请手动复制基金代码。', 'error')
    }
  }

  function handleAnalysisAction() {
    if (analysisStatus === 'done' || analysisStatus === 'loading') {
      onViewAnalysis(item.code)
      return
    }
    onAnalyze(item.code)
  }

  return <article
    ref={selectedRef}
    aria-label={`${item.name}候选基金`}
    className={`flex min-w-0 flex-col overflow-hidden rounded-lg border transition-colors ${selected ? 'border-accent/65 bg-accent/[0.06]' : 'border-border bg-base/55 hover:border-accent/35'}`}
  >
    <div className="flex-1 p-3">
      <div className="flex items-start justify-between gap-2.5">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold leading-[18px] text-foreground">{item.name || '基金名称未提供'}</div>
          <div className="mt-0.5 font-mono text-[11px] leading-[18px] text-muted">{copyCode}</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {rank != null && <span className="rounded border border-accent/25 bg-accent/10 px-1.5 py-0.5 text-[11px] leading-4 text-accent">{rankLabel || `榜单顺序 #${rank}`}</span>}
            {item.share_class && <span className="rounded border border-accent/20 bg-accent/10 px-1.5 py-0.5 text-[11px] leading-4 text-accent">{item.share_class} 类</span>}
            {category && <span className="rounded border border-border/70 bg-elevated/60 px-1.5 py-0.5 text-[11px] leading-4 text-secondary">{category}</span>}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-[11px] leading-4 text-muted">单位净值</div>
          <div className="font-mono text-sm leading-[18px] tabular-nums text-foreground">{item.nav == null || !Number.isFinite(item.nav) ? '未提供' : item.nav.toFixed(4)}</div>
          <div className={`mt-0.5 text-[11px] leading-[18px] tabular-nums ${growth == null ? 'text-muted' : growth >= 0 ? 'text-bull' : 'text-bear'}`}>
            {HORIZON_LABELS[candidateHorizon]} {formatFundCandidateReturn(growth)}
          </div>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border/60 pt-2 text-[11px] leading-[18px] text-secondary">
        <span>来源 {source}</span>
        <span>净值日期 {navDateLabel}</span>
        <span className={`rounded-full border px-1.5 py-0.5 text-[11px] leading-4 ${analysisTone}`}>AI {analysisLabel}</span>
        <span>{modelLabel}</span>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-1.5">
        {metrics.map((metric) => <div
          key={metric.label}
          title={metric.title}
          className={`min-w-0 rounded-md border border-border/70 bg-surface/70 px-2 py-1.5 ${metric.wide ? 'col-span-2' : ''}`}
        >
          <div className="truncate text-[11px] leading-4 text-muted">{metric.label}</div>
          <div className={`break-words text-[11px] leading-[18px] ${metric.tone === 'positive' ? 'text-bull' : metric.tone === 'negative' ? 'text-bear' : 'text-foreground'}`}>{metric.value}</div>
        </div>)}
      </div>

      <div className="mt-2">
        <div className="mb-1 text-[11px] leading-4 text-muted">定期报告披露股票</div>
        {holdingItems.length
          ? <div className="flex flex-wrap gap-1">
            {holdingItems.slice(0, 5).map((holding) => <span
              key={`${holding.thscode}-${holding.name}`}
              title={holding.hold_ratio == null ? holding.name : `${holding.name} · 披露占比 ${formatPercent(holding.hold_ratio)}`}
              className="max-w-full truncate rounded border border-cyan-400/20 bg-cyan-400/10 px-1.5 py-0.5 text-[11px] leading-4 text-cyan-300"
            >{holding.name || holding.thscode}</span>)}
            {holdingItems.length > 5 && <span className="rounded border border-border/70 px-1.5 py-0.5 text-[11px] leading-4 text-muted">+{holdingItems.length - 5} 只</span>}
          </div>
          : <div className="text-[11px] leading-[18px] text-muted">{item.research ? '未提供定期报告持仓' : '待获取定期报告持仓'}</div>}
      </div>

      <div className="mt-2 line-clamp-2 text-[11px] leading-[18px] text-warning" title={gap}>资料缺口：{gap}</div>
      {item.purchase_fee_text != null && <div className="mt-1 truncate text-[11px] leading-[18px] text-secondary" title={`榜单申购费参考：${item.purchase_fee_text || '未提供'}`}>
        榜单申购费参考：{item.purchase_fee_text || '未提供'}
      </div>}

      <div className="mt-2 rounded-md border border-border/60 bg-surface/45 px-2 py-1.5 text-[11px] leading-[18px] text-secondary">
        <span className="font-medium text-foreground">候选依据：</span>{fundCandidateBasis(item, resultFundType, candidateHorizon)}
      </div>
      {researchSummaryText && <div className="mt-1.5 rounded-md border border-border/60 bg-surface/45 px-2 py-1.5 text-[11px] leading-[18px] text-secondary">
        <span className="font-medium text-foreground">资料摘要：</span><span className="line-clamp-2">{researchSummaryText}</span>
      </div>}
      <div className="mt-1.5 rounded-md border border-accent/15 bg-accent/[0.04] px-2 py-1.5 text-[11px] leading-[18px] text-secondary">
        <span className="font-medium text-accent">{analysisPresentation.label}</span>
        <span className="line-clamp-2">{analysisPresentation.text}</span>
        {analysisStatus === 'error' && hasAnalysisContent && analysisError && <span className="mt-0.5 block truncate text-danger" title={analysisError}>失败原因：{analysisError}</span>}
      </div>
    </div>

    <div className="grid grid-cols-4 gap-1.5 border-t border-border/60 px-3 py-2">
      <button
        type="button"
        aria-pressed={selected}
        aria-label={`${selected ? '已选择' : '选择'}基金 ${item.name}，代码 ${copyCode}`}
        onClick={() => onSelect(item.code)}
        className={`inline-flex min-h-11 whitespace-nowrap sm:min-h-9 w-full items-center justify-center gap-1 rounded-md border px-1 sm:px-2.5 text-[11px] leading-[18px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${selected ? 'border-accent/35 bg-accent/10 text-accent' : 'border-border text-secondary hover:text-foreground'}`}
      >{selected && <Check className="hidden h-3.5 w-3.5 sm:block" />}{selected ? '已选中' : '选择候选'}</button>
      <button
        type="button"
        disabled={modelConfigured === false && analysisStatus !== 'done' && analysisStatus !== 'loading'}
        onClick={handleAnalysisAction}
        title={modelConfigured === false && analysisStatus !== 'done' && analysisStatus !== 'loading' ? '请先配置 AI 模型' : actionLabel}
        className="inline-flex min-h-11 whitespace-nowrap sm:min-h-9 w-full items-center justify-center gap-1.5 rounded-md bg-accent/10 px-1 sm:px-2.5 text-xs font-medium leading-[18px] text-accent hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        {analysisStatus === 'loading' ? <Loader2 className="hidden h-3.5 w-3.5 animate-spin sm:block" /> : analysisStatus === 'done' ? <Check className="hidden h-3.5 w-3.5 sm:block" /> : <Sparkles className="hidden h-3.5 w-3.5 sm:block" />}
        {actionLabel}
      </button>
      <Link
        to={detailsUrl}
        aria-label={`查看${item.name}基金详情`}
        className="inline-flex min-h-11 whitespace-nowrap sm:min-h-9 w-full items-center justify-center rounded-md border border-border px-1 sm:px-2.5 text-[11px] leading-[18px] text-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >详情</Link>
      <button
        type="button"
        onClick={handleCopyCode}
        className="inline-flex min-h-11 whitespace-nowrap sm:min-h-9 w-full items-center justify-center gap-1 rounded-md border border-border px-1 sm:px-2.5 text-[11px] leading-[18px] text-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        {copyFeedback === 'copied' ? <Check className="hidden h-3.5 w-3.5 text-bull sm:block" /> : <Copy className="hidden h-3.5 w-3.5 sm:block" />}
        {copyFeedback === 'copied' ? '已复制' : '复制代码'}
      </button>
      {modelConfigured === false && analysisStatus !== 'done' && analysisStatus !== 'loading' && <Link to="/settings?tab=ai" className="col-span-2 text-[11px] leading-[18px] text-warning underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">配置 AI 模型后可分析</Link>}
      {copyFeedback === 'error' && <span role="status" className="col-span-2 text-[11px] leading-[18px] text-danger">复制失败，请手动复制基金代码 {copyCode}。</span>}
      {copyFeedback === 'copied' && <span role="status" className="sr-only col-span-2">已复制基金代码 {copyCode}</span>}
    </div>
  </article>
}
