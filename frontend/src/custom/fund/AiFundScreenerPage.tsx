import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Activity, ArrowUpRight, Clock3, Database, Loader2, Sparkles } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { api } from '@/lib/api'
import { fundApi, type FundRankItem } from './client'
import { FundResearchPanel, rememberFundResearch } from './FundResearchPanel'

const FUND_TYPES = ['股票型', '混合型', '指数型', '债券型']
const HORIZONS = [
  { value: '1m', label: '近 1 月' },
  { value: '3m', label: '近 3 月' },
  { value: '6m', label: '近 6 月' },
  { value: '1y', label: '近 1 年' },
  { value: '2y', label: '近 2 年' },
  { value: '3y', label: '近 3 年' },
]
const RESULT_STORAGE_KEY = 'stockfund.ai-fund-screener.result.v1'

type SavedResult = {
  fundType: string
  horizon: string
  share: string
  candidates: FundRankItem[]
  selectedCode: string | null
  report: string
  retrievedAt: number | null
  resultHorizon: string
}

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
        (value.selectedCode !== null && typeof value.selectedCode !== 'string') ||
        (value.retrievedAt !== null && typeof value.retrievedAt !== 'number')) return null
    return value
  } catch {
    return null
  }
}

function pct(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function metric(item: FundRankItem, horizon: string): number | null {
  return item[`growth_${horizon}` as keyof FundRankItem] as number | null
}

function sameFundCode(candidateCode: string, eventCode: string): boolean {
  const normalizedEventCode = eventCode.toUpperCase().replace(/\.OF$/, '')
  return candidateCode.toUpperCase().replace(/\.OF$/, '') === normalizedEventCode
}

export function AiFundScreenerPage() {
  const [saved] = useState(loadSavedResult)
  const [fundType, setFundType] = useState(saved?.fundType ?? '混合型')
  const [horizon, setHorizon] = useState(saved?.horizon ?? '1y')
  const [share, setShare] = useState(saved?.share ?? 'all')
  const [phase, setPhase] = useState<'idle' | 'loading' | 'done' | 'error'>(saved ? 'done' : 'idle')
  const [candidates, setCandidates] = useState<FundRankItem[]>(saved?.candidates ?? [])
  const [selectedCode, setSelectedCode] = useState<string | null>(saved?.selectedCode ?? null)
  const [report, setReport] = useState(saved?.report ?? '')
  const [error, setError] = useState('')
  const [retrievedAt, setRetrievedAt] = useState<number | null>(saved?.retrievedAt ?? null)
  const [resultHorizon, setResultHorizon] = useState(saved?.resultHorizon ?? '1y')
  const [researchProgress, setResearchProgress] = useState<{ completed: number; total: number; code?: string } | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const model = useQuery({ queryKey: ['ai-fund-model-status'], queryFn: api.strategyAiStatus, staleTime: 60_000 })

  useEffect(() => () => controllerRef.current?.abort(), [])
  useEffect(() => {
    if (phase !== 'done' || !candidates.length) return
    const result: SavedResult = { fundType, horizon, share, candidates, selectedCode, report, retrievedAt, resultHorizon }
    try {
      window.sessionStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify(result))
    } catch {
      // Storage may be disabled; the page remains usable without persistence.
    }
  }, [phase, fundType, horizon, share, candidates, selectedCode, report, retrievedAt, resultHorizon])
  const selected = useMemo(
    () => candidates.find((candidate) => candidate.code === selectedCode) ?? candidates[0] ?? null,
    [candidates, selectedCode],
  )
  const horizonLabel = HORIZONS.find((option) => option.value === resultHorizon)?.label ?? resultHorizon

  async function run() {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setCandidates([])
    setSelectedCode(null)
    setReport('')
    setError('')
    setRetrievedAt(null)
    setResultHorizon(horizon)
    setResearchProgress(null)
    setPhase('loading')
    try {
      for await (const event of fundApi.aiPickStream({ fund_type: fundType, horizon, share }, controller.signal)) {
        if (event.type === 'meta') {
          const items = event.candidates ?? []
          setCandidates(items)
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
          setReport((current) => current + (event.content ?? ''))
        } else if (event.type === 'error') {
          setError(event.message || 'AI 选基暂不可用')
          setPhase('error')
          return
        } else if (event.type === 'done') {
          setPhase('done')
        }
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
    <div className="space-y-4 px-5 py-5 md:px-8">
      <div className="rounded-xl border border-accent/25 bg-accent/[0.06] px-4 py-3 text-xs leading-5 text-secondary">
        <span className="font-medium text-foreground">研究候选：</span>候选来自东方财富公开基金排名数据，再由已配置的 AI 模型进行比较。每只基金单独标注净值日期；费率、回撤和持仓按实际来源覆盖情况展示，缺失项会明确列出。模型输出仅用于继续核对，不能据此交易。
        {model.data && !model.data.configured && <span className="mt-1 block text-warning">尚未配置 AI 模型。请先到 <Link className="text-accent underline" to="/settings?tab=ai">AI 设置</Link>连接模型。</span>}
      </div>

      <section className="rounded-xl border border-border bg-surface p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-36 flex-1 space-y-1 text-xs text-secondary"><span>基金类型</span><select aria-label="基金类型" value={fundType} onChange={(event) => setFundType(event.target.value)} className="h-10 w-full rounded-md border border-border bg-base px-3 text-sm text-foreground">{FUND_TYPES.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label className="min-w-32 flex-1 space-y-1 text-xs text-secondary"><span>对比区间</span><select aria-label="对比区间" value={horizon} onChange={(event) => setHorizon(event.target.value)} className="h-10 w-full rounded-md border border-border bg-base px-3 text-sm text-foreground">{HORIZONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
          <label className="min-w-28 flex-1 space-y-1 text-xs text-secondary"><span>份额类别</span><select aria-label="份额类别" value={share} onChange={(event) => setShare(event.target.value)} className="h-10 w-full rounded-md border border-border bg-base px-3 text-sm text-foreground"><option value="all">全部</option><option value="A">A 类</option><option value="C">C 类</option></select></label>
          <button onClick={run} disabled={phase === 'loading' || model.data?.configured === false} className="inline-flex h-10 min-w-36 items-center justify-center gap-2 rounded-btn bg-accent px-4 text-sm font-medium text-white disabled:opacity-50"><Sparkles className="h-4 w-4" />{phase === 'loading' ? '正在分析…' : '生成研究候选'}</button>
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

      {!!candidates.length && <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_350px]">
        <section className="min-w-0 rounded-xl border border-border bg-surface">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3"><div className="text-sm font-semibold">历史榜单候选 <span className="text-xs font-normal text-muted">{candidates.length} 只</span></div><div className="flex items-center gap-1 text-[11px] text-muted"><Database className="h-3.5 w-3.5" />东方财富公开基金排名</div></div>
          <div className="grid gap-3 p-3 md:grid-cols-2">
            {candidates.map((item) => {
              const value = metric(item, resultHorizon)
              return <button
                key={item.code}
                onClick={() => setSelectedCode(item.code)}
                className={`min-w-0 rounded-lg border p-3 text-left transition-colors ${selected?.code === item.code ? 'border-accent/65 bg-accent/[0.07]' : 'border-border bg-base/40 hover:border-accent/35'}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0"><div className="truncate text-sm font-semibold text-foreground">{item.name}</div><div className="font-mono text-[11px] text-muted">{item.code}.OF</div></div>
                  {item.share_class && <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[11px] text-accent">{item.share_class} 类</span>}
                </div>
                <div className="mt-3 flex items-end justify-between"><span className="text-xs text-secondary">{horizonLabel}</span><span className={`font-mono text-lg ${value == null ? 'text-muted' : value >= 0 ? 'text-bull' : 'text-bear'}`}>{pct(value)}</span></div>
                <div className="mt-2 text-[11px] text-muted">近 1 月 {pct(item.growth_1m)} · 近 1 年 {pct(item.growth_1y)}</div>
                <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 border-t border-border/50 pt-2 text-[10px] text-secondary">
                  <span>单位净值 {item.nav == null || !Number.isFinite(item.nav) ? '—' : item.nav.toFixed(4)}</span>
                  <span>净值日期 {item.nav_date ?? '未知'}</span>
                  {item.purchase_fee_text != null && <span>榜单申购费 {item.purchase_fee_text || '—'}</span>}
                </div>
              </button>
            })}
          </div>
        </section>
        <aside className="rounded-xl border border-border bg-surface p-4 xl:sticky xl:top-4 xl:self-start">{selected && <><div className="text-lg font-semibold text-foreground">{selected.name}</div><div className="font-mono text-xs text-muted">{selected.code}.OF</div><div className="mt-4 grid grid-cols-2 gap-2">{HORIZONS.map((period) => <div key={period.value} className="rounded-md border border-border bg-base/30 p-2"><div className="text-[11px] text-muted">{period.label}</div><div className="mt-1 font-mono text-sm text-foreground">{pct(metric(selected, period.value))}</div></div>)}</div><div className="mt-4"><FundResearchPanel thscode={`${selected.code}.OF`} horizon={resultHorizon} research={selected.research} purchaseFeeText={selected.purchase_fee_text} enabled={phase !== 'loading'} /></div><Link to={`/fund?code=${encodeURIComponent(`${selected.code}.OF`)}&name=${encodeURIComponent(selected.name)}&research_horizon=${encodeURIComponent(resultHorizon)}`} className="mt-4 inline-flex items-center gap-1 rounded-btn border border-accent/35 bg-accent/10 px-3 py-2 text-xs text-accent hover:bg-accent/20">查看基金详情 <ArrowUpRight className="h-3.5 w-3.5" /></Link></>}</aside>
      </div>}

      {!!candidates.length && <section className="rounded-xl border border-border bg-surface p-4"><div className="mb-3 flex items-center gap-2 text-sm font-semibold"><Activity className="h-4 w-4 text-accent" />AI 对比说明</div>{phase === 'loading' && researchProgress && researchProgress.completed >= researchProgress.total && <div className="mb-2 flex items-center gap-2 text-xs text-secondary"><Loader2 className="h-3.5 w-3.5 animate-spin" />模型分析中…</div>}{report ? <MarkdownRenderer content={report} /> : <div className="text-sm text-muted">等待模型说明…</div>}<div className="mt-4 flex items-center gap-1 text-[11px] text-muted"><Clock3 className="h-3.5 w-3.5" />本次获取：{retrievedAt ? new Date(retrievedAt).toLocaleString() : '—'}；净值日期按基金分别展示。</div></section>}
    </div>
  </>
}
