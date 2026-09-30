import { useEffect, useState } from 'react'
import { ExternalLink, Loader2, RefreshCw } from 'lucide-react'
import { fundApi, type FeeRule, type FundResearch } from './client'

const RESEARCH_CACHE_TTL_MS = 6 * 60 * 60 * 1000
const DEGRADED_RESEARCH_CACHE_TTL_MS = 60 * 1000
const MAX_CACHED_FUNDS = 64
const researchCache = new Map<string, Map<string, FundResearch>>()

function cacheKey(thscode: string, horizon: string): string {
  return `${thscode.toUpperCase()}|${horizon}`
}

/** Keep streamed research available when the user opens the same fund detail. */
export function rememberFundResearch(research: FundResearch, horizon = research.horizon): void {
  const now = Date.now()
  pruneResearchCache(now)
  if (!isResearchFresh(research, now)) return
  const thscode = research.thscode.toUpperCase()
  const byHorizon = researchCache.get(thscode) ?? new Map<string, FundResearch>()
  const cached = byHorizon.get(horizon)
  if (cached && cached.retrieved_at_ms > research.retrieved_at_ms) return
  byHorizon.delete(horizon)
  byHorizon.set(horizon, research)
  researchCache.delete(thscode)
  researchCache.set(thscode, byHorizon)
  while (researchCache.size > MAX_CACHED_FUNDS) {
    const oldestThscode = researchCache.keys().next().value
    if (oldestThscode === undefined) break
    researchCache.delete(oldestThscode)
  }
}

function isResearchFresh(research: FundResearch, now = Date.now()): boolean {
  const age = now - research.retrieved_at_ms
  return Number.isFinite(research.retrieved_at_ms) && age >= 0 && age < researchCacheTtlMs(research)
}

function researchCacheTtlMs(research: FundResearch): number {
  const allUnavailable = research.fees.status === 'unavailable'
    && research.risk.status === 'unavailable'
    && research.holdings.status === 'unavailable'
  return research.warnings.length > 0 || allUnavailable
    ? DEGRADED_RESEARCH_CACHE_TTL_MS
    : RESEARCH_CACHE_TTL_MS
}

function pruneResearchCache(now = Date.now()): void {
  for (const [thscode, byHorizon] of researchCache) {
    for (const [horizon, research] of byHorizon) {
      if (!isResearchFresh(research, now)) byHorizon.delete(horizon)
    }
    if (byHorizon.size === 0) researchCache.delete(thscode)
  }
}

function getCachedFundResearch(thscode: string, horizon: string): FundResearch | null {
  pruneResearchCache()
  const normalizedThscode = thscode.toUpperCase()
  const byHorizon = researchCache.get(normalizedThscode)
  const research = byHorizon?.get(horizon) ?? null
  if (research && byHorizon) {
    researchCache.delete(normalizedThscode)
    researchCache.set(normalizedThscode, byHorizon)
  }
  return research
}

function valuePct(value: number | null): string {
  return value === null || !Number.isFinite(value) ? '—' : `${value.toFixed(2)}%`
}

function sourceLink(url: string, label = '查看来源') {
  if (!/^https?:\/\//i.test(url)) return null
  return <a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline">{label}<ExternalLink className="h-3 w-3" /></a>
}

function statusLabel(status: string): string {
  if (status === 'ok') return '可用'
  if (status === 'partial') return '部分可用'
  return '暂无数据'
}

function StatusPill({ status }: { status: string }) {
  const classes = status === 'ok'
    ? 'border-bull/30 bg-bull/10 text-bull'
    : status === 'partial'
      ? 'border-warning/30 bg-warning/10 text-warning'
      : 'border-border bg-base/50 text-muted'
  return <span className={`rounded-full border px-2 py-0.5 text-[10px] ${classes}`}>{statusLabel(status)}</span>
}

function FeeRuleList({ title, rules }: { title: string; rules: FeeRule[] }) {
  return <div>
    <h4 className="mb-1 text-[11px] font-medium text-secondary">{title}</h4>
    {rules.length ? <ul className="space-y-1.5">
      {rules.map((rule, index) => <li key={`${rule.condition}-${index}`} className="rounded-md border border-border/70 bg-base/40 p-2 text-[11px] leading-4">
        <div className="font-medium text-foreground">{rule.condition || '适用条件未提供'}</div>
        <div className="mt-0.5 text-secondary">费率：{rule.rate_text || '—'}</div>
        <div className="text-muted">平台折扣：{rule.discount_text ?? '未提供'}</div>
      </li>)}
    </ul> : <p className="text-[11px] text-muted">暂无可用规则</p>}
  </div>
}

const FIELD_LABELS: Record<string, string> = {
  nav: '单位净值',
  nav_date: '净值日期',
  management_pct: '管理费',
  custody_pct: '托管费',
  sales_service_pct: '销售服务费',
  subscription_rules: '申购费规则',
  redemption_rules: '赎回费规则',
  max_drawdown_pct: '最大回撤',
  holdings: '基金持仓',
  coverage_weight_pct: '持仓覆盖比例',
}

function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field.replaceAll('_', ' ')
}

function FundResearchContent({ research, purchaseFeeText, analysisSnapshot }: { research: FundResearch; purchaseFeeText?: string | null; analysisSnapshot: boolean }) {
  const { fees, risk, holdings } = research
  const riskBasis = risk.basis === 'source_return_series'
    ? '来源累计收益序列（图表抽样）'
    : risk.basis === 'unit_nav'
      ? '单位净值（未复权）'
      : '口径未知'

  return <div className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="text-sm font-semibold text-foreground">基金基础研究</div>
      <div className="text-[10px] text-muted">东方财富 · 区间 {research.horizon} · 更新于 {new Date(research.retrieved_at_ms).toLocaleString()}</div>
    </div>
    {analysisSnapshot && <p className="text-[10px] leading-4 text-muted">本次分析资料快照与当前 AI 说明对应，不会自动替换；来源暂不可用时，可重新生成研究候选再次获取。</p>}

    <div className="grid grid-cols-3 gap-1.5 text-center">
      <div className="rounded-md border border-border/70 bg-base/40 p-1.5"><div className="text-[10px] text-muted">费率</div><StatusPill status={fees.status} /></div>
      <div className="rounded-md border border-border/70 bg-base/40 p-1.5"><div className="text-[10px] text-muted">回撤</div><StatusPill status={risk.status} /></div>
      <div className="rounded-md border border-border/70 bg-base/40 p-1.5"><div className="text-[10px] text-muted">持仓</div><StatusPill status={holdings.status} /></div>
    </div>

    {purchaseFeeText != null && <div className="rounded-md border border-border/70 bg-base/40 px-2.5 py-2 text-[11px]">
      <span className="text-muted">榜单申购费参考：</span><span className="font-medium text-foreground">{purchaseFeeText || '—'}</span>
    </div>}

    <section className="rounded-lg border border-border/70 p-3">
      <div className="mb-2 flex items-center justify-between gap-2"><h3 className="text-xs font-semibold text-foreground">费率</h3>{sourceLink(fees.source_url)}</div>
      <div className="mb-3 grid grid-cols-3 gap-2">
        <div><div className="text-[10px] text-muted">管理费 / 年</div><div className="font-mono text-xs text-foreground">{valuePct(fees.management_pct)}</div></div>
        <div><div className="text-[10px] text-muted">托管费 / 年</div><div className="font-mono text-xs text-foreground">{valuePct(fees.custody_pct)}</div></div>
        <div><div className="text-[10px] text-muted">销售服务费 / 年</div><div className="font-mono text-xs text-foreground">{valuePct(fees.sales_service_pct)}</div></div>
      </div>
      <div className="mb-2 text-[10px] text-secondary">申购 {fees.subscription_rules.length} 档 · 赎回 {fees.redemption_rules.length} 档</div>
      {fees.note && <p className="mb-2 text-[10px] leading-4 text-muted">{fees.note}</p>}
      <details className="rounded-md border border-border/60 bg-base/30 px-2.5 py-2">
        <summary className="cursor-pointer text-[11px] font-medium text-secondary">查看申购、赎回条件和平台折扣</summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <FeeRuleList title="申购条件与费率" rules={fees.subscription_rules} />
          <FeeRuleList title="赎回条件与费率" rules={fees.redemption_rules} />
        </div>
      </details>
    </section>

    <section className="rounded-lg border border-border/70 p-3">
      <div className="mb-2 flex items-center justify-between gap-2"><h3 className="text-xs font-semibold text-foreground">区间观测最大回撤</h3>{sourceLink(risk.source_url)}</div>
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div><div className="text-muted">回撤幅度</div><div className="font-mono text-sm text-foreground">{valuePct(risk.max_drawdown_pct)}</div></div>
        <div><div className="text-muted">计算口径</div><div className="text-foreground">{riskBasis}</div></div>
        <div><div className="text-muted">统计窗口</div><div className="text-foreground">{risk.start_date ?? '—'} 至 {risk.end_date ?? '—'}</div></div>
        <div><div className="text-muted">观测点</div><div className="font-mono text-foreground">{Number.isFinite(risk.observations) ? risk.observations : '—'}</div></div>
      </div>
      {risk.basis === 'source_return_series' && <p className="mt-2 text-[10px] leading-4 text-warning">来源累计收益图表按采样点估算，并非逐日精确最大回撤。</p>}
      {risk.basis === 'unit_nav' && <p className="mt-2 text-[10px] leading-4 text-warning">使用未复权单位净值；分红会影响该回撤结果。</p>}
      {risk.note && <p className="mt-1 text-[10px] leading-4 text-muted">{risk.note}</p>}
    </section>

    <section className="rounded-lg border border-border/70 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><h3 className="text-xs font-semibold text-foreground">定期报告持仓</h3>{sourceLink(holdings.source_url)}</div>
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
        <span><span className="text-muted">报告期：</span><span className="text-foreground">{holdings.report_date ?? '—'}</span></span>
        <span><span className="text-muted">已披露股票权重：</span><span className="font-mono text-foreground">{valuePct(holdings.coverage_weight_pct)}</span></span>
      </div>
      <div className="mb-1 text-[10px] text-muted">已披露股票 {holdings.items.length} 只；基金已披露股票可能超过 10 只，列表超过 10 只时可展开查看其余项目。</div>
      {holdings.items.length ? <>
        <ul className="divide-y divide-border/50">
        {holdings.items.slice(0, 10).map((item) => <li key={`${item.thscode}-${item.name}`} className="flex items-center justify-between gap-2 py-1.5 text-[11px]">
          <span className="min-w-0 truncate text-foreground">{item.name}<span className="ml-1.5 font-mono text-[10px] text-muted">{item.thscode}</span></span>
          <span className="shrink-0 font-mono text-secondary">{valuePct(item.hold_ratio)}</span>
        </li>)}
        </ul>
        {holdings.items.length > 10 && <details className="mt-2 rounded-md border border-border/60 bg-base/30 px-2.5 py-2">
          <summary className="cursor-pointer text-[11px] font-medium text-secondary">展开其余 {holdings.items.length - 10} 只已披露股票</summary>
          <ul className="mt-2 divide-y divide-border/50">
            {holdings.items.slice(10).map((item) => <li key={`${item.thscode}-${item.name}`} className="flex items-center justify-between gap-2 py-1.5 text-[11px]">
              <span className="min-w-0 truncate text-foreground">{item.name}<span className="ml-1.5 font-mono text-[10px] text-muted">{item.thscode}</span></span>
              <span className="shrink-0 font-mono text-secondary">{valuePct(item.hold_ratio)}</span>
            </li>)}
          </ul>
        </details>}
      </> : <p className="text-[11px] text-muted">暂无可用持仓明细</p>}
      {holdings.report_note && <p className="mt-2 text-[10px] leading-4 text-muted">{holdings.report_note}</p>}
      {holdings.related_reports.length > 0 && <div className="mt-3 border-t border-border/50 pt-2">
        <h4 className="mb-1 text-[11px] font-medium text-secondary">相关公告与报告</h4>
        <p className="mb-1 text-[10px] leading-4 text-muted">下列日期是相关报告公告日期，不代表已核验持仓的精确披露日。</p>
        <ul className="space-y-1.5">
          {holdings.related_reports.map((report, index) => <li key={`${report.title}-${report.publication_date}-${index}`} className="text-[11px] leading-4">
            {sourceLink(report.source_url, report.title || '查看公告') ?? <span className="text-foreground">{report.title || '相关报告'}</span>}
            <span className="ml-2 text-muted">公告 {report.publication_date} · 报告期 {report.report_date}</span>
          </li>)}
        </ul>
      </div>}
    </section>

    {research.missing_fields.length > 0 && <div className="rounded-md border border-warning/20 bg-warning/5 p-2 text-[10px] leading-4 text-secondary">
      <span className="font-medium text-warning">未取到：</span>{research.missing_fields.map(fieldLabel).join('、')}
    </div>}
    {research.warnings.length > 0 && <ul className="space-y-1 text-[10px] leading-4 text-muted">
      {research.warnings.map((warning, index) => <li key={`${warning}-${index}`}>• {warning}</li>)}
    </ul>}
  </div>
}

export function FundResearchPanel({
  thscode,
  horizon = '1y',
  research: streamedResearch,
  purchaseFeeText,
  enabled = true,
}: {
  thscode: string
  horizon?: string
  research?: FundResearch
  purchaseFeeText?: string | null
  enabled?: boolean
}) {
  const key = cacheKey(thscode, horizon)
  const [loaded, setLoaded] = useState<{ key: string; research: FundResearch } | null>(null)
  const [request, setRequest] = useState<{ key: string; loading: boolean; error: string }>({ key, loading: false, error: '' })
  const [retry, setRetry] = useState(0)
  const analysisSnapshot = streamedResearch != null
  const currentStreamedResearch = streamedResearch ?? null
  const research = currentStreamedResearch ?? (loaded?.key === key && isResearchFresh(loaded.research) ? loaded.research : null)
  const loading = request.key === key && request.loading
  const error = request.key === key ? request.error : ''

  useEffect(() => {
    let alive = true
    if (currentStreamedResearch) {
      rememberFundResearch(currentStreamedResearch, horizon)
      setLoaded({ key, research: currentStreamedResearch })
      setRequest({ key, loading: false, error: '' })
      return () => { alive = false }
    }
    const cached = getCachedFundResearch(thscode, horizon)
    if (cached) {
      setLoaded({ key, research: cached })
      setRequest({ key, loading: false, error: '' })
      return () => { alive = false }
    }
    if (!enabled) {
      setRequest({ key, loading: false, error: '' })
      return () => { alive = false }
    }
    setRequest({ key, loading: true, error: '' })
    fundApi.research(thscode, horizon).then((result) => {
      if (!alive) return
      rememberFundResearch(result, horizon)
      setLoaded({ key, research: result })
      setRequest({ key, loading: false, error: '' })
    }).catch((cause) => {
      if (!alive) return
      setRequest({ key, loading: false, error: cause instanceof Error ? cause.message : '基金研究资料加载失败' })
    })
    return () => { alive = false }
  }, [currentStreamedResearch, enabled, horizon, key, retry, thscode])

  useEffect(() => {
    if (!research || analysisSnapshot) return
    const expiresIn = Math.max(0, research.retrieved_at_ms + researchCacheTtlMs(research) - Date.now())
    const timer = window.setTimeout(() => setRetry((current) => current + 1), expiresIn + 1)
    return () => window.clearTimeout(timer)
  }, [analysisSnapshot, key, research])

  return <section className="rounded-xl border border-border bg-surface p-4">
    {research ? <FundResearchContent research={research} purchaseFeeText={purchaseFeeText} analysisSnapshot={analysisSnapshot} /> : <div>
      <div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold text-foreground">基金基础研究</h2>
        {loading && <span className="inline-flex items-center gap-1 text-[11px] text-muted"><Loader2 className="h-3 w-3 animate-spin" />加载中</span>}
      </div>
      {loading && <p className="mt-2 text-[11px] text-secondary">正在读取费率、历史回撤和定期报告持仓。</p>}
      {!loading && !enabled && <p className="mt-2 text-[11px] text-muted">研究资料将随本次候选研究结果返回。</p>}
      {error && <div role="alert" className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-danger/30 bg-danger/10 p-2 text-[11px] text-danger">
        <span>{error}</span><button onClick={() => setRetry((current) => current + 1)} className="inline-flex items-center gap-1 rounded border border-danger/30 px-2 py-1"><RefreshCw className="h-3 w-3" />重试</button>
      </div>}
    </div>}
  </section>
}
