import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Activity, ArrowUpRight, Clock3, Database, Loader2, Sparkles } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { api } from '@/lib/api'
import { fundApi, type AiFundType, type FundProfile, type FundRankItem } from './client'
import { FundResearchPanel, rememberFundResearch } from './FundResearchPanel'

const FUND_TYPES: Array<{ value: AiFundType; label: string }> = [
  { value: 'all', label: '全部类型' },
  { value: '股票型', label: '股票型' },
  { value: '混合型', label: '混合型' },
  { value: '指数型', label: '指数型' },
  { value: '债券型', label: '债券型' },
]
const HORIZONS = [
  { value: '1m', label: '近 1 月' },
  { value: '3m', label: '近 3 月' },
  { value: '6m', label: '近 6 月' },
  { value: '1y', label: '近 1 年' },
  { value: '2y', label: '近 2 年' },
  { value: '3y', label: '近 3 年' },
]
const RESULT_STORAGE_KEY = 'stockfund.ai-fund-screener.result.v1'
const MAX_SAVED_FUND_ANALYSES = 24
const MAX_SAVED_FUND_ANALYSIS_CHARS = 30_000

function isAiFundType(value: string): value is AiFundType {
  return FUND_TYPES.some((option) => option.value === value)
}

type AiFundCategory = Exclude<AiFundType, 'all'>

function isAiFundCategory(value: string): value is AiFundCategory {
  return value !== 'all' && isAiFundType(value)
}

type SavedResult = {
  fundType: string
  horizon: string
  share: string
  unavailableTypes?: string[]
  candidates: FundRankItem[]
  selectedCode: string | null
  report: string
  retrievedAt: number | null
  resultHorizon: string
  resultFundType?: AiFundType
  fundAnalyses?: Record<string, PersistedFundAnalysis>
}

type DetailTab = 'profile' | 'analysis' | 'compare'

type FundAiAnalysis = {
  status: 'idle' | 'loading' | 'done' | 'error' | 'cancelled'
  summary: string
  content: string
  error?: string
  generatedAt?: number
  navDate?: string | null
  holdingsReportDate?: string | null
}

type PersistedFundAnalysis = Pick<FundAiAnalysis, 'summary' | 'content' | 'generatedAt' | 'navDate' | 'holdingsReportDate'> & {
  status: 'done'
}

const DETAIL_TABS: Array<{ value: DetailTab; label: string }> = [
  { value: 'profile', label: '基金资料' },
  { value: 'analysis', label: 'AI 分析' },
  { value: 'compare', label: '整体对比' },
]

function loadSavedResult(): SavedResult | null {
  try {
    const raw = window.sessionStorage.getItem(RESULT_STORAGE_KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as SavedResult
    if (!Array.isArray(value.candidates) || value.candidates.length === 0 ||
        !value.candidates.every((item) => item && typeof item.code === 'string' && typeof item.name === 'string') ||
        typeof value.report !== 'string' ||
        typeof value.fundType !== 'string' || typeof value.horizon !== 'string' ||
        typeof value.share !== 'string' || typeof value.resultHorizon !== 'string' ||
        (value.resultFundType !== undefined && !isAiFundType(value.resultFundType)) ||
        (value.unavailableTypes !== undefined && (!Array.isArray(value.unavailableTypes) ||
          !value.unavailableTypes.every((item) => typeof item === 'string' && isAiFundCategory(item)))) ||
        (value.selectedCode !== null && typeof value.selectedCode !== 'string') ||
        (value.retrievedAt !== null && typeof value.retrievedAt !== 'number')) return null
    const fundAnalyses = saveableFundAnalyses(value.fundAnalyses ?? {})
    return { ...value, fundAnalyses }
  } catch {
    return null
  }
}

function saveableFundAnalyses(analyses: unknown): Record<string, PersistedFundAnalysis> {
  if (!analyses || typeof analyses !== 'object' || Array.isArray(analyses)) return {}
  const completed = Object.entries(analyses as Record<string, unknown>)
    .filter(([code, value]) => {
      if (!code.trim() || !value || typeof value !== 'object' || Array.isArray(value)) return false
      const analysis = value as Partial<FundAiAnalysis>
      return analysis.status === 'done' && typeof analysis.content === 'string' && analysis.content.trim() &&
        typeof analysis.generatedAt === 'number' && Number.isFinite(analysis.generatedAt)
    })
    .sort(([, first], [, second]) => {
      const firstTime = (first as FundAiAnalysis).generatedAt ?? 0
      const secondTime = (second as FundAiAnalysis).generatedAt ?? 0
      return secondTime - firstTime
    })
    .slice(0, MAX_SAVED_FUND_ANALYSES)

  const result: Record<string, PersistedFundAnalysis> = {}
  for (const [code, value] of completed) {
    const analysis = value as FundAiAnalysis
    const next = {
      ...result,
      [code]: {
        status: 'done' as const,
        summary: analysis.summary,
        content: analysis.content,
        generatedAt: analysis.generatedAt,
        navDate: analysis.navDate ?? null,
        holdingsReportDate: analysis.holdingsReportDate ?? null,
      },
    }
    if (JSON.stringify(next).length <= MAX_SAVED_FUND_ANALYSIS_CHARS) result[code] = next[code]
  }
  return result
}

function pct(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function metric(item: FundRankItem, horizon: string): number | null {
  return item[`growth_${horizon}` as keyof FundRankItem] as number | null
}

function valuePct(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(2)}%`
}

function ReadinessBadge({
  label,
  status,
  loading = false,
}: {
  label: string
  status?: 'ok' | 'partial' | 'unavailable'
  loading?: boolean
}) {
  const tone = status === 'ok'
    ? 'border-bull/25 bg-bull/10 text-bull'
    : status === 'partial'
      ? 'border-warning/25 bg-warning/10 text-warning'
      : 'border-border bg-base/60 text-muted'
  const state = loading ? '读取中' : status === 'ok' ? '可用' : status === 'partial' ? '部分' : status === 'unavailable' ? '暂无' : '待获取'
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] leading-4 ${tone}`} title={`${label}：${state}`}>{label} {state}</span>
}

function ProfileFacts({ profile, candidate }: { profile: FundProfile; candidate: FundRankItem }) {
  const rows: Array<[string, string | null | undefined]> = [
    ['基金全称', profile.fund_name],
    ['基金公司', profile.mgmt_name],
    ['基金经理', profile.manager_name],
    ['基金类型', profile.fund_type ?? candidate.fund_type],
    ['成立日期', profile.estab_date],
    ['业绩基准', profile.benchmark],
  ]
  return <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
    {rows.map(([label, value]) => <div key={label} className="min-w-0 rounded-lg border border-border/70 bg-base/45 px-3 py-2">
      <dt className="text-[10px] text-muted">{label}</dt>
      <dd className="mt-1 break-words text-xs text-foreground">{value || '—'}</dd>
    </div>)}
  </dl>
}

function sameFundCode(candidateCode: string, eventCode: string): boolean {
  const normalizedEventCode = eventCode.toUpperCase().replace(/\.OF$/, '')
  return candidateCode.toUpperCase().replace(/\.OF$/, '') === normalizedEventCode
}

export function AiFundScreenerPage() {
  const [saved] = useState(loadSavedResult)
  const [fundType, setFundType] = useState<AiFundType>(() =>
    saved && isAiFundType(saved.fundType) ? saved.fundType : 'all',
  )
  const [horizon, setHorizon] = useState(saved?.horizon ?? '1y')
  const [share, setShare] = useState(saved?.share ?? 'all')
  const [phase, setPhase] = useState<'idle' | 'loading' | 'done' | 'error'>(saved ? 'done' : 'idle')
  const [candidates, setCandidates] = useState<FundRankItem[]>(saved?.candidates ?? [])
  const [unavailableTypes, setUnavailableTypes] = useState<AiFundCategory[]>(() =>
    (saved?.unavailableTypes ?? []).filter(isAiFundCategory),
  )
  const [selectedCode, setSelectedCode] = useState<string | null>(saved?.selectedCode ?? null)
  const [report, setReport] = useState(saved?.report ?? '')
  const [error, setError] = useState('')
  const [retrievedAt, setRetrievedAt] = useState<number | null>(saved?.retrievedAt ?? null)
  const [resultHorizon, setResultHorizon] = useState(saved?.resultHorizon ?? '1y')
  const [resultFundType, setResultFundType] = useState<AiFundType>(() =>
    saved?.resultFundType ?? (saved && isAiFundType(saved.fundType) ? saved.fundType : 'all'),
  )
  const [researchProgress, setResearchProgress] = useState<{ completed: number; total: number; code?: string } | null>(null)
  const [detailTab, setDetailTab] = useState<DetailTab>('compare')
  const [fundAnalysisByCode, setFundAnalysisByCode] = useState<Record<string, FundAiAnalysis>>(() => saved?.fundAnalyses ?? {})
  const controllerRef = useRef<AbortController | null>(null)
  const analysisControllerRef = useRef<{ code: string; controller: AbortController } | null>(null)
  const model = useQuery({ queryKey: ['ai-fund-model-status'], queryFn: api.strategyAiStatus, staleTime: 60_000 })

  useEffect(() => () => {
    controllerRef.current?.abort()
    analysisControllerRef.current?.controller.abort()
  }, [])
  useEffect(() => {
    if (phase !== 'done' || !candidates.length) return
    const result: SavedResult = {
      fundType, horizon, share, unavailableTypes, candidates, selectedCode, report, retrievedAt,
      resultHorizon, resultFundType, fundAnalyses: saveableFundAnalyses(fundAnalysisByCode),
    }
    try {
      window.sessionStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify(result))
    } catch {
      // Storage may be disabled; the page remains usable without persistence.
    }
  }, [phase, fundType, horizon, share, unavailableTypes, candidates, selectedCode, report, retrievedAt, resultHorizon, resultFundType, fundAnalysisByCode])
  const selected = useMemo(
    () => candidates.find((candidate) => candidate.code === selectedCode) ?? candidates[0] ?? null,
    [candidates, selectedCode],
  )
  const selectedThscode = selected ? `${selected.code}.OF` : ''
  const selectedAnalysis = selected ? fundAnalysisByCode[selected.code] : undefined
  const persistedFundAnalyses = useMemo(() => saveableFundAnalyses(fundAnalysisByCode), [fundAnalysisByCode])
  const selectedAnalysisIsPersisted = !!selected && !!persistedFundAnalyses[selected.code]
  const profileQuery = useQuery({
    queryKey: ['ai-fund-profile', selectedThscode],
    queryFn: () => fundApi.profile(selectedThscode),
    enabled: detailTab === 'profile' && !!selectedThscode,
    staleTime: 5 * 60_000,
  })
  const horizonLabel = HORIZONS.find((option) => option.value === resultHorizon)?.label ?? resultHorizon

  function selectCandidate(code: string) {
    const active = analysisControllerRef.current
    if (active && active.code !== code) {
      active.controller.abort()
      analysisControllerRef.current = null
      setFundAnalysisByCode((current) => {
        const analysis = current[active.code]
        return analysis?.status === 'loading'
          ? { ...current, [active.code]: { ...analysis, status: 'cancelled' } }
          : current
      })
    }
    setSelectedCode(code)
  }

  async function generateSelectedAnalysis() {
    if (!selected || !selectedThscode) return
    const previous = analysisControllerRef.current
    if (previous) {
      previous.controller.abort()
      setFundAnalysisByCode((current) => {
        const analysis = current[previous.code]
        return analysis?.status === 'loading'
          ? { ...current, [previous.code]: { ...analysis, status: 'cancelled' } }
          : current
      })
    }

    const code = selected.code
    const controller = new AbortController()
    analysisControllerRef.current = { code, controller }
    setFundAnalysisByCode((current) => ({
      ...current,
      [code]: {
        status: 'loading', summary: '', content: '', error: '',
        navDate: null,
        holdingsReportDate: null,
      },
    }))

    try {
      for await (const event of fundApi.analyzeStream(selectedThscode, undefined, controller.signal)) {
        if (controller.signal.aborted) return
        if (event.type === 'meta') {
          setFundAnalysisByCode((current) => ({
            ...current,
            [code]: {
              ...(current[code] ?? { status: 'loading', summary: '', content: '', error: '' }),
              summary: event.summary ?? '',
              navDate: event.nav_date ?? null,
              holdingsReportDate: event.holdings_report_date ?? null,
            },
          }))
        } else if (event.type === 'delta') {
          setFundAnalysisByCode((current) => {
            const analysis = current[code] ?? { status: 'loading', summary: '', content: '', error: '' }
            return { ...current, [code]: { ...analysis, content: analysis.content + (event.content ?? '') } }
          })
        } else if (event.type === 'error') {
          setFundAnalysisByCode((current) => {
            const analysis = current[code] ?? { status: 'loading', summary: '', content: '', error: '' }
            return { ...current, [code]: { ...analysis, status: 'error', error: event.message || '分析失败' } }
          })
          return
        } else if (event.type === 'done') {
          setFundAnalysisByCode((current) => {
            const analysis = current[code] ?? { status: 'loading', summary: '', content: '', error: '' }
            return analysis.content.trim()
              ? { ...current, [code]: { ...analysis, status: 'done', generatedAt: Date.now(), error: '' } }
              : { ...current, [code]: { ...analysis, status: 'error', error: '分析流已完成，但没有返回分析内容；请重试。' } }
          })
          return
        }
      }
      if (!controller.signal.aborted) {
        setFundAnalysisByCode((current) => {
          const analysis = current[code]
          return analysis?.status === 'loading'
            ? { ...current, [code]: { ...analysis, status: 'error', error: '分析流结束但未收到完成标记，结果不完整；请重试。' } }
            : current
        })
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setFundAnalysisByCode((current) => {
          const analysis = current[code] ?? { status: 'loading', summary: '', content: '', error: '' }
          return {
            ...current,
            [code]: {
              ...analysis,
              status: 'error',
              error: cause instanceof Error ? cause.message : '分析失败',
            },
          }
        })
      }
    } finally {
      if (analysisControllerRef.current?.controller === controller) analysisControllerRef.current = null
    }
  }

  function cancelSelectedAnalysis() {
    const active = analysisControllerRef.current
    if (!selected || active?.code !== selected.code) return
    active.controller.abort()
    analysisControllerRef.current = null
    setFundAnalysisByCode((current) => {
      const analysis = current[selected.code]
      return analysis?.status === 'loading'
        ? { ...current, [selected.code]: { ...analysis, status: 'cancelled' } }
        : current
    })
  }

  async function run() {
    controllerRef.current?.abort()
    analysisControllerRef.current?.controller.abort()
    analysisControllerRef.current = null
    setFundAnalysisByCode({})
    setDetailTab('compare')
    const controller = new AbortController()
    controllerRef.current = controller
    setCandidates([])
    setUnavailableTypes([])
    setSelectedCode(null)
    setReport('')
    setError('')
    setRetrievedAt(null)
    setResultHorizon(horizon)
    setResultFundType(fundType)
    setResearchProgress(null)
    setPhase('loading')
    let sawDone = false
    let reportText = ''
    let candidateCount = 0
    try {
      for await (const event of fundApi.aiPickStream({ fund_type: fundType, horizon, share }, controller.signal)) {
        if (event.type === 'meta') {
          const items = event.candidates ?? []
          candidateCount = items.length
          setCandidates(items)
          setUnavailableTypes(event.unavailable_types ?? [])
          setSelectedCode(items[0]?.code ?? null)
          setRetrievedAt(event.retrieved_at_ms ?? null)
          setResearchProgress({ completed: items.filter((item) => !!item.research).length, total: items.length })
        } else if (event.type === 'research') {
          const research = event.research
          if (research) {
            rememberFundResearch(research, horizon)
            const code = event.code ?? research.thscode
            setCandidates((current) => current.map((item) => sameFundCode(item.code, code)
              ? { ...item, research }
              : item))
          }
          setResearchProgress((current) => ({
            completed: event.completed ?? (current?.completed ?? 0) + 1,
            total: event.total ?? current?.total ?? 0,
            code: event.code,
          }))
        } else if (event.type === 'delta') {
          const content = event.content ?? ''
          reportText += content
          setReport((current) => current + content)
        } else if (event.type === 'error') {
          setError(event.message || 'AI 选基暂不可用')
          setPhase('error')
          return
        } else if (event.type === 'done') {
          sawDone = true
          break
        }
      }
      if (controller.signal.aborted) return
      if (!sawDone) {
        setError('AI 选基数据流结束但未收到完成标记，结果不完整；请重试。')
        setPhase('error')
      } else if (!reportText.trim()) {
        setError(candidateCount ? 'AI 选基已结束，但没有返回整体分析内容；请重试。' : '本次没有可分析的候选或整体说明；请调整条件后重试。')
        setPhase('error')
      } else {
        setPhase('done')
      }
    } catch (cause) {
      if (controller.signal.aborted) return
      setError(cause instanceof Error ? cause.message : 'AI 选基请求失败')
      setPhase('error')
    }
  }

  return <>
    <PageHeader title="AI 选基" className="pl-14 sm:pl-5" subtitle={<span className="hidden sm:inline">历史收益候选 · 模型辅助对比 · 数据缺口透明展示</span>} right={
      <Link to="/fund" className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:text-foreground">基金中心</Link>
    } />
    <div className="space-y-3 px-4 py-3 md:px-8">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-accent/20 bg-accent/[0.05] px-3 py-2 text-[10px] leading-4 text-secondary">
        <span><span className="font-medium text-foreground">研究候选</span> · 公开排名与模型辅助比较 · 数据缺口按来源标示 · 不构成交易指令</span>
        {fundType === 'all' && <span className="text-muted">全部含股票、混合、指数、债券榜单，按类别抽取。</span>}
        {model.data && !model.data.configured && <span className="text-warning">尚未配置 AI 模型，前往 <Link className="text-accent underline" to="/settings?tab=ai">AI 设置</Link>连接。</span>}
      </div>

      <section className="rounded-xl border border-border bg-surface p-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <label className="space-y-1 text-[10px] text-secondary"><span>基金类型</span><select aria-label="基金类型" value={fundType} onChange={(event) => setFundType(event.target.value as AiFundType)} className="h-9 w-full rounded-md border border-border bg-base px-2.5 text-xs text-foreground">{FUND_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
          <label className="space-y-1 text-[10px] text-secondary"><span>对比区间</span><select aria-label="对比区间" value={horizon} onChange={(event) => setHorizon(event.target.value)} className="h-9 w-full rounded-md border border-border bg-base px-2.5 text-xs text-foreground">{HORIZONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
          <label className="space-y-1 text-[10px] text-secondary"><span>份额类别</span><select aria-label="份额类别" value={share} onChange={(event) => setShare(event.target.value)} className="h-9 w-full rounded-md border border-border bg-base px-2.5 text-xs text-foreground"><option value="all">全部</option><option value="A">A 类</option><option value="C">C 类</option></select></label>
          <button onClick={run} disabled={phase === 'loading' || model.data?.configured === false} className="mt-auto inline-flex h-9 items-center justify-center gap-2 rounded-btn bg-accent px-3 text-xs font-medium text-white disabled:opacity-50"><Sparkles className="h-3.5 w-3.5" />{phase === 'loading' ? '正在分析…' : '生成研究候选'}</button>
        </div>
      </section>

      {phase === 'idle' && <div className="rounded-xl border border-border bg-surface p-8 text-center text-sm text-secondary">选择基金类型、历史区间和份额类别，然后生成候选与分析。</div>}
      {phase === 'loading' && !candidates.length && <div className="flex items-center justify-center gap-2 rounded-xl border border-border bg-surface p-8 text-sm text-secondary"><Loader2 className="h-4 w-4 animate-spin" />正在读取基金榜单…</div>}
      {phase === 'loading' && candidates.length > 0 && <div role="status" aria-live="polite" className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-xs text-secondary">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {researchProgress && researchProgress.completed < researchProgress.total
          ? `正在读取基金研究资料：${researchProgress.completed}/${researchProgress.total}${researchProgress.code ? ` · ${researchProgress.code}` : ''}`
          : '基金资料已读取，正在生成 AI 对比说明…'}
      </div>}
      {error && <div role="alert" className="rounded-xl border border-danger/30 bg-danger/10 p-4 text-sm text-danger">{error}</div>}
      {!!unavailableTypes.length && <div role="status" className="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">以下类型榜单暂不可用，本次只展示已返回的类别：{unavailableTypes.join('、')}。</div>}

      {!!candidates.length && <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_336px]">
        <section className="min-w-0 rounded-xl border border-border bg-surface">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
            <div className="text-sm font-semibold">历史榜单候选 <span className="text-xs font-normal text-muted">{candidates.length} 只</span></div>
            <div className="flex items-center gap-1 text-[11px] text-muted"><Database className="h-3.5 w-3.5" />东方财富公开基金排名</div>
          </div>
          <div className="grid gap-2.5 p-3 md:grid-cols-2 xl:grid-cols-3">
            {candidates.map((item) => {
              const value = metric(item, resultHorizon)
              const research = item.research
              const risk = research?.risk
              const drawdown = risk?.max_drawdown_pct
              const navDate = item.nav_date ?? research?.nav_date
              return <button
                key={item.code}
                type="button"
                aria-pressed={selected?.code === item.code}
                onClick={() => selectCandidate(item.code)}
                className={`min-w-0 rounded-lg border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${selected?.code === item.code ? 'border-accent/65 bg-accent/[0.08] shadow-sm' : 'border-border bg-base/40 hover:border-accent/35 hover:bg-elevated/40'}`}
              >
                <div className="flex min-h-10 items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-foreground">{item.name}</div>
                    <div className="mt-0.5 font-mono text-[10px] text-muted">{item.code}.OF</div>
                  </div>
                  <div className="flex shrink-0 flex-wrap justify-end gap-1">
                    {item.share_class && <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">{item.share_class} 类</span>}
                    {resultFundType === 'all' && item.fund_type && <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-secondary">{item.fund_type}</span>}
                  </div>
                </div>
                <div className="mt-2.5 flex items-end justify-between gap-2">
                  <span className="text-[11px] text-secondary">{horizonLabel}</span>
                  <span className={`font-mono text-lg tabular-nums ${value == null ? 'text-muted' : value >= 0 ? 'text-bull' : 'text-bear'}`}>{pct(value)}</span>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted">
                  <span>1 月 {pct(item.growth_1m)}</span><span>1 年 {pct(item.growth_1y)}</span>
                  <span>净值 {item.nav == null || !Number.isFinite(item.nav) ? '—' : item.nav.toFixed(4)}</span>
                  <span>日期 {navDate ?? '未知'}</span>
                </div>
                {item.purchase_fee_text != null && <div className="mt-1 truncate text-[10px] text-secondary" title={`榜单申购费参考：${item.purchase_fee_text || '—'}`}>榜单申购费 {item.purchase_fee_text || '—'}</div>}
                <div className="mt-2.5 flex flex-wrap gap-1.5 border-t border-border/60 pt-2">
                  <ReadinessBadge label={`回撤 ${valuePct(drawdown)}`} status={risk?.status} loading={!research && phase === 'loading'} />
                  <ReadinessBadge label="费率" status={research?.fees.status} loading={!research && phase === 'loading'} />
                  <ReadinessBadge label="持仓" status={research?.holdings.status} loading={!research && phase === 'loading'} />
                </div>
              </button>
            })}
          </div>
        </section>

        <aside aria-label="基金详细分析" className="min-w-0 rounded-xl border border-border bg-surface p-4 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:overflow-y-auto">
          {selected && <>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-base font-semibold text-foreground">{selected.name}</div>
                <div className="mt-0.5 font-mono text-[11px] text-muted">{selected.code}.OF <span className="font-sans">· 净值日期 {selected.nav_date ?? selected.research?.nav_date ?? '未知'}</span></div>
              </div>
              <Link to={`/fund?code=${encodeURIComponent(selectedThscode)}&name=${encodeURIComponent(selected.name)}&research_horizon=${encodeURIComponent(resultHorizon)}`} aria-label="查看基金详情" title="查看基金详情" className="inline-flex h-9 shrink-0 items-center gap-1 rounded-btn border border-accent/35 bg-accent/10 px-2.5 text-[11px] text-accent hover:bg-accent/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                详情 <ArrowUpRight className="h-3.5 w-3.5" />
              </Link>
            </div>

            <div className="mt-3 grid grid-cols-3 gap-1.5">
              {HORIZONS.map((period) => {
                const value = metric(selected, period.value)
                return <div key={period.value} className="rounded-md border border-border/70 bg-base/35 px-2 py-1.5">
                  <div className="text-[9px] text-muted">{period.label}</div>
                  <div className={`mt-0.5 truncate font-mono text-[11px] tabular-nums ${value == null ? 'text-muted' : value >= 0 ? 'text-bull' : 'text-bear'}`}>{pct(value)}</div>
                </div>
              })}
            </div>

            <div role="tablist" aria-label="基金分析面板" className="mt-4 grid grid-cols-3 rounded-lg border border-border bg-base/50 p-1">
              {DETAIL_TABS.map((tab) => <button
                key={tab.value}
                id={`fund-detail-tab-${tab.value}`}
                type="button"
                role="tab"
                aria-selected={detailTab === tab.value}
                aria-controls="fund-detail-panel"
                onClick={() => setDetailTab(tab.value)}
                tabIndex={detailTab === tab.value ? 0 : -1}
                onKeyDown={(event) => {
                  if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
                  event.preventDefault()
                  const currentIndex = DETAIL_TABS.findIndex((item) => item.value === tab.value)
                  const offset = event.key === 'ArrowRight' ? 1 : -1
                  const next = DETAIL_TABS[(currentIndex + offset + DETAIL_TABS.length) % DETAIL_TABS.length]
                  setDetailTab(next.value)
                  document.getElementById(`fund-detail-tab-${next.value}`)?.focus()
                }}
                className={`min-h-11 rounded-md px-1.5 text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${detailTab === tab.value ? 'bg-elevated text-foreground shadow-sm' : 'text-muted hover:text-secondary'}`}
              >{tab.label}</button>)}
            </div>

            <div id="fund-detail-panel" role="tabpanel" aria-labelledby={`fund-detail-tab-${detailTab}`} className="mt-3 min-w-0">
              {detailTab === 'profile' && <div className="space-y-3">
                <section className="rounded-lg border border-border/70 bg-base/25 p-3">
                  <div className="mb-2 text-xs font-semibold text-foreground">基本资料</div>
                  {profileQuery.isLoading && <div role="status" className="text-[11px] text-muted">正在读取基金资料…</div>}
                  {profileQuery.isError && <div role="alert" className="text-[11px] text-danger">{profileQuery.error instanceof Error ? profileQuery.error.message : '基金资料读取失败'}</div>}
                  {!profileQuery.isLoading && !profileQuery.isError && profileQuery.data?.profile
                    ? <ProfileFacts profile={profileQuery.data.profile} candidate={selected} />
                    : !profileQuery.isLoading && !profileQuery.isError && <div className="text-[11px] text-muted">暂无基本资料，已返回字段之外不作推断。</div>}
                </section>
                <FundResearchPanel thscode={selectedThscode} horizon={resultHorizon} research={selected.research} purchaseFeeText={selected.purchase_fee_text} enabled={phase !== 'loading'} />
              </div>}

              {detailTab === 'analysis' && <section className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-xs font-semibold text-foreground">单只基金 AI 分析</div>
                  {selectedAnalysis?.status === 'loading'
                    ? <button type="button" onClick={cancelSelectedAnalysis} className="inline-flex min-h-11 items-center gap-1.5 rounded-md border border-danger/30 px-2.5 text-[11px] text-danger hover:bg-danger/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger"><span>停止接收</span></button>
                    : <button type="button" onClick={generateSelectedAnalysis} disabled={model.data?.configured === false} className="inline-flex min-h-11 items-center gap-1.5 rounded-md bg-accent px-2.5 text-[11px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                      <Sparkles className="h-3.5 w-3.5" />{selectedAnalysis?.status === 'done' ? '重新生成' : selectedAnalysis?.status === 'error' ? '重试分析' : '生成分析'}
                    </button>}
                </div>
                {model.data?.configured === false && <div className="rounded-md border border-warning/30 bg-warning/10 px-2.5 py-2 text-[10px] text-warning">尚未配置 AI 模型，请先到 <Link className="underline" to="/settings?tab=ai">AI 设置</Link>连接模型。</div>}
                {selectedAnalysis?.status === 'loading' && <div role="status" aria-live="polite" className="flex items-center gap-2 text-[11px] text-secondary"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在接收分析内容；可停止接收。</div>}
                {selectedAnalysis?.status === 'cancelled' && <div role="status" className="text-[11px] text-muted">已停止接收；已有片段按基金代码保留，后端处理可能仍在继续。</div>}
                {selectedAnalysis?.status === 'error' && <div role="alert" className="rounded-md border border-danger/30 bg-danger/10 px-2.5 py-2 text-[11px] text-danger">{selectedAnalysis.error}</div>}
                {selectedAnalysis?.status === 'done' && <div className="rounded-md border border-border/70 bg-base/40 px-2.5 py-2 text-[10px] leading-4 text-muted">
                  完成于 {selectedAnalysis.generatedAt ? new Date(selectedAnalysis.generatedAt).toLocaleString() : '时间未知'} · 分析净值日期 {selectedAnalysis.navDate || '未提供'} · 披露持仓报告期 {selectedAnalysis.holdingsReportDate || '未提供'}
                  {!selectedAnalysisIsPersisted && <span className="block text-warning">本条超出恢复缓存容量上限；留在当前页面仍可查看。</span>}
                </div>}
                {selectedAnalysis?.summary && <div className="rounded-md border border-border/70 bg-base/40 px-2.5 py-2 text-[10px] leading-4 text-secondary">{selectedAnalysis.summary}</div>}
                {selectedAnalysis?.content
                  ? <div className="max-h-[58vh] overflow-y-auto rounded-lg border border-border/70 bg-base/35 p-3"><MarkdownRenderer content={selectedAnalysis.content} /></div>
                  : <div className="rounded-lg border border-dashed border-border px-3 py-8 text-center text-[11px] text-muted">按需生成所选基金分析；切换候选时各自内容分开保留。</div>}
                <p className="text-[10px] leading-4 text-muted">研究情景仅供核对。场外基金按未知成交净值确认；不构成下单指令，不会自动交易，也不保证收益。</p>
              </section>}

              {detailTab === 'compare' && <section>
                <div className="mb-3 flex items-center gap-2 text-xs font-semibold text-foreground"><Activity className="h-4 w-4 text-accent" />整体对比说明</div>
                {phase === 'loading' && researchProgress && researchProgress.completed < researchProgress.total && <div role="status" className="mb-2 flex items-center gap-2 text-[11px] text-secondary"><Loader2 className="h-3.5 w-3.5 animate-spin" />资料读取 {researchProgress.completed}/{researchProgress.total}</div>}
                {phase === 'loading' && researchProgress && researchProgress.completed >= researchProgress.total && <div role="status" className="mb-2 flex items-center gap-2 text-[11px] text-secondary"><Loader2 className="h-3.5 w-3.5 animate-spin" />整体模型分析中…</div>}
                {report ? <MarkdownRenderer content={report} /> : <div className="rounded-lg border border-dashed border-border px-3 py-8 text-center text-[11px] text-muted">等待本次候选的整体对比说明。</div>}
                <div className="mt-4 flex items-center gap-1 border-t border-border/70 pt-3 text-[10px] text-muted"><Clock3 className="h-3.5 w-3.5" />抓取时间：{retrievedAt ? new Date(retrievedAt).toLocaleString() : '—'}；净值日期按基金分别展示。</div>
              </section>}
            </div>
          </>}
        </aside>
      </div>}
    </div>
  </>
}
