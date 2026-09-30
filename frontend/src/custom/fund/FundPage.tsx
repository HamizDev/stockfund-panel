/**
 * 基金中心页面 — 场外基金净值 + ETF/LOF 行情。
 *
 * 自包含扩展页面: 数据全部走 /api/custom/fund, 状态用组件内 state +
 * 手动轮询, 不碰核心 queryKeys / api.ts / 全局 store。删除本目录即卸载。
 *
 * 样式: 与主面板 Dashboard 一致 (accent 色条标题 / surface 卡片 /
 * 紧凑字号 / 数字 font-mono)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import * as echarts from 'echarts'
import type { ECharts } from 'echarts'
import { ArrowLeft, BarChart3, Info, LineChart, Plus, Search, Star, Activity, Sparkles, X, Loader2 } from 'lucide-react'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { PageHeader } from '@/components/PageHeader'
import { FundScreener } from './FundScreener'
import { FundResearchPanel } from './FundResearchPanel'
import { cn } from '@/lib/cn'
import {
  fundApi,
  pctText,
  type FundEstimate,
  type FundEstimateCurve,
  type FundDataSource,
  type FundNavPoint,
  type FundPortfolioItem,
  type FundProfile,
  type FundQuote,
  type FundSearchItem,
  type FundWatchItem,
  type HoldingItem,
} from './client'

const QUOTE_POLL_MS = 15000

function isTradable(kind: string): boolean {
  return kind === 'ETF' || kind === 'LOF'
}

/** 主面板风格区块标题: accent 色条 + Icon + text-xs 标题 */
function SectionTitle({ icon: Icon, title }: { icon: typeof Activity; title: string }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      <span className="h-4 w-0.5 rounded-full bg-gradient-to-b from-accent to-accent/30" />
      <Icon className="h-4 w-4 text-accent" />
      <h2 className="text-sm font-semibold text-foreground">{title}</h2>
    </div>
  )
}

/** 主面板风格卡片 */
function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className={cn('rounded-xl border border-border bg-surface p-4', className)}>
      {children}
    </div>
  )
}

function useWatchlist() {
  const [items, setItems] = useState<FundWatchItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const refresh = useCallback(async () => {
    try {
      const r = await fundApi.watchlist()
      setItems(r.items)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    refresh()
  }, [refresh])
  return { items, loading, error, refresh }
}

function usePortfolio() {
  const [items, setItems] = useState<FundPortfolioItem[]>([])
  const [loading, setLoading] = useState(true)
  const refresh = useCallback(async () => {
    try {
      const r = await fundApi.portfolio()
      setItems(r.items)
    } catch {
      /* 忽略, 持仓为空时不展示错误 */
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    refresh()
  }, [refresh])
  return { items, loading, refresh, setItems }
}

function NavChart({ nav }: { nav: FundNavPoint[] }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    if (!chartRef.current) chartRef.current = echarts.init(ref.current)
    const chart = chartRef.current
    const dates = nav.map((p) => p.nav_date)
    const hasAdjustedNav = nav.some((point) => point.adj_nav != null)
    chart.setOption({
      animation: false,
      tooltip: { trigger: 'axis', textStyle: { fontSize: 11 } },
      legend: { data: hasAdjustedNav ? ['单位净值', '复权净值'] : ['单位净值'], textStyle: { color: '#9aa4b2', fontSize: 10 } },
      grid: { left: 40, right: 12, top: 26, bottom: 22 },
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 9 } },
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 9 } },
      series: [
        {
          name: '单位净值',
          type: 'line',
          showSymbol: false,
          data: nav.map((p) => p.unit_nav),
          lineStyle: { color: '#4f8cff', width: 1.5 },
        },
        ...(hasAdjustedNav ? [{
          name: '复权净值',
          type: 'line',
          showSymbol: false,
          data: nav.map((p) => p.adj_nav),
          lineStyle: { color: '#f0a35e', width: 1.5 },
        }] : []),
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [nav])

  useEffect(() => () => {
    chartRef.current?.dispose()
    chartRef.current = null
  }, [])

  return <div ref={ref} className="h-52 w-full" />
}

/** 当日估值走势图 (穿透估算, 分钟级) */
function EstimateChart({ curve }: { curve: FundEstimateCurve }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    if (!chartRef.current) chartRef.current = echarts.init(ref.current)
    const chart = chartRef.current
    const pts = curve.points
    const times = pts.map((p) => `${p.time.slice(0, 2)}:${p.time.slice(2)}`)
    const vals = pts.map((p) => p.change_pct * 100)
    // 0% 居中: 取最大绝对值, 上下对称
    const maxAbs = Math.max(0.5, ...vals.map((v) => Math.abs(v)))
    const yMax = Math.ceil(maxAbs * 1.1 * 100) / 100
    chart.setOption({
      animation: false,
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: 11 },
        formatter: (params: { dataIndex: number }[]) => {
          const i = params[0]?.dataIndex ?? 0
          const p = pts[i]
          if (!p) return ''
          const up = p.change_pct >= 0
          return `${times[i]}<br/>涨跌 <span style="color:${up ? '#e5484d' : '#2f9e6e'}">${up ? '+' : ''}${(p.change_pct * 100).toFixed(2)}%</span><br/>估算净值 ${p.est_nav?.toFixed(4) ?? '—'}`
        },
      },
      grid: { left: 44, right: 12, top: 12, bottom: 22 },
      xAxis: {
        type: 'category',
        data: times,
        axisLabel: { fontSize: 9, interval: Math.floor(times.length / 6) },
      },
      yAxis: {
        type: 'value',
        min: -yMax,
        max: yMax,
        axisLabel: { fontSize: 9, formatter: (v: number) => `${v.toFixed(2)}%` },
      },
      series: [
        {
          name: '估算涨跌幅',
          type: 'line',
          showSymbol: false,
          data: pts.map((p) => +(p.change_pct * 100).toFixed(3)),
          lineStyle: { color: '#4f8cff', width: 1.5 },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: '#9aa4b2', type: 'dashed', width: 1 },
            label: { fontSize: 9, formatter: '0%' },
            data: [{ yAxis: 0 }],
          },
        },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [curve])

  useEffect(() => () => {
    chartRef.current?.dispose()
    chartRef.current = null
  }, [])

  return <div ref={ref} className="h-48 w-full" />
}

/** 场外基金当日估值卡片 (穿透自算) */
function EstimateCard({
  estimate,
  curve,
  prevNav,
  navDate,
  holdings,
}: {
  estimate: FundEstimate
  curve: FundEstimateCurve
  prevNav: number | null
  navDate: string | null
  holdings: { items: HoldingItem[]; report_note: string }
}) {
  const [showHoldings, setShowHoldings] = useState(false)
  if (estimate.status !== 'ok') {
    return (
      <Card>
        <SectionTitle icon={Activity} title="当日估值" />
        <div className="py-4 text-center text-[11px] text-muted">{estimate.note}</div>
      </Card>
    )
  }
  const up = (estimate.change_pct ?? 0) >= 0
  return (
    <Card>
      <div className="mb-1.5 flex items-center justify-between">
        <SectionTitle icon={Activity} title="当日估值 (穿透)" />
        <span className="text-[10px] text-muted">估算·非官方</span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-lg font-bold tabular-nums">
          {estimate.est_nav?.toFixed(4) ?? '—'}
        </span>
        <span className={cn('font-mono text-[11px] tabular-nums', up ? 'text-bull' : 'text-bear')}>
          {up ? '+' : ''}
          {pctText(estimate.change_pct)}
        </span>
        <span className="text-[10px] text-muted">
          昨收 {prevNav?.toFixed(4) ?? '—'}{navDate ? ` (${navDate})` : ''}
        </span>
      </div>
      {curve.status === 'ok' && curve.points.length > 0 ? (
        <EstimateChart curve={curve} />
      ) : (
        <div className="py-4 text-center text-[11px] text-muted">{curve.note}</div>
      )}
      <div className="mt-1 flex items-center justify-between">
        <span className="text-[10px] text-muted">{estimate.note}</span>
        <button
          onClick={() => setShowHoldings((v) => !v)}
          className="text-[10px] text-accent hover:underline"
        >
          {showHoldings ? '收起持仓' : `持仓明细 (${holdings.items.length})`}
        </button>
      </div>
      {showHoldings && (
        <div className="mt-1.5 space-y-1 border-t border-border pt-1.5">
          {(estimate.details ?? []).map((d) => {
            const dUp = d.change_pct >= 0
            return (
              <div key={d.thscode} className="flex items-center justify-between text-[11px]">
                <span className="text-foreground">
                  {d.name}
                  <span className="ml-1 font-mono text-[10px] text-muted">{d.thscode}</span>
                </span>
                <span className="flex items-center gap-2 font-mono tabular-nums">
                  <span className={dUp ? 'text-bull' : 'text-bear'}>
                    {dUp ? '+' : ''}
                    {pctText(d.change_pct)}
                  </span>
                  <span className="text-muted">{d.hold_ratio.toFixed(2)}%</span>
                </span>
              </div>
            )
          })}
          <div className="text-[10px] text-muted">{holdings.report_note}</div>
        </div>
      )}
    </Card>
  )
}

function QuoteCard({ quote }: { quote: FundQuote }) {
  const up = (quote.change_pct ?? 0) >= 0
  return (
    <Card>
      <SectionTitle icon={Activity} title="实时行情" />
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-lg font-bold tabular-nums">
          {quote.last_price?.toFixed(3) ?? '—'}
        </span>
        <span className={cn('font-mono text-[11px] tabular-nums', up ? 'text-bull' : 'text-bear')}>
          {quote.change_amount !== null && quote.change_amount !== undefined
            ? `${up ? '+' : ''}${quote.change_amount.toFixed(3)}`
            : '—'}{' '}
          {pctText(quote.change_pct)}
        </span>
      </div>
      <div className="mt-1.5 grid grid-cols-4 gap-1.5 text-[11px]">
        {[
          ['开盘', quote.open?.toFixed(3)],
          ['最高', quote.high?.toFixed(3)],
          ['最低', quote.low?.toFixed(3)],
          ['昨收', quote.prev_close?.toFixed(3)],
          ['振幅', pctText(quote.amplitude)],
          ['换手', pctText(quote.turnover_rate)],
          ['成交量(手)', quote.volume?.toLocaleString()],
          ['成交额', quote.amount ? `${(quote.amount / 1e8).toFixed(2)}亿` : undefined],
        ].map(([k, v]) => (
          <div key={k}>
            <div className="text-[10px] text-muted">{k}</div>
            <div className="font-mono tabular-nums">{v ?? '—'}</div>
          </div>
        ))}
      </div>
    </Card>
  )
}

function ProfileBlock({ profile }: { profile: FundProfile }) {
  const rows: [string, string | null][] = [
    ['基金全称', profile.fund_name],
    ['基金公司', profile.mgmt_name],
    ['基金经理', profile.manager_name],
    ['成立日期', profile.estab_date],
    ['基金类型', profile.fund_type],
  ]
  return (
    <Card>
      <SectionTitle icon={Info} title="基本资料" />
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex gap-1.5">
            <dt className="shrink-0 text-muted">{k}</dt>
            <dd className="truncate">{v ?? '—'}</dd>
          </div>
        ))}
      </dl>
      {profile.benchmark && (
        <div className="mt-1 text-[10px] text-muted">业绩基准: {profile.benchmark}</div>
      )}
    </Card>
  )
}

/** 加入持仓对话框: 填写持仓金额和持有收益 */
function AddPositionDialog({
  thscode,
  name,
  onClose,
  onSaved,
}: {
  thscode: string
  name: string
  onClose: () => void
  onSaved: () => void
}) {
  const [amount, setAmount] = useState('')
  const [profit, setProfit] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  const save = async () => {
    const amt = parseFloat(amount)
    if (!amt || amt <= 0) {
      setErr('请填写有效的持仓金额')
      return
    }
    setSaving(true)
    setErr('')
    try {
      const r = await fundApi.portfolio()
      const items = r.items.filter((it) => it.thscode !== thscode)
      items.push({ thscode, name, amount: amt, profit: parseFloat(profit) || 0 })
      await fundApi.savePortfolio(items)
      onSaved()
    } catch (e) {
      setErr(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="w-full max-w-xs rounded-lg bg-base p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold">加入持仓</h3>
          <button onClick={onClose} className="text-muted hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="mb-1 text-[11px] text-muted">{name} ({thscode})</div>
        <label className="mb-1 block text-[11px] text-muted">持仓金额 (元)</label>
        <input
          type="number"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="如 1447.55"
          className="mb-2 h-8 w-full rounded-md border border-border/40 bg-white px-2 text-[12px] text-gray-900 outline-none focus:border-accent"
        />
        <label className="mb-1 block text-[11px] text-muted">持有收益 (元, 可为负)</label>
        <input
          type="number"
          value={profit}
          onChange={(e) => setProfit(e.target.value)}
          placeholder="如 -4.05"
          className="mb-3 h-8 w-full rounded-md border border-border/40 bg-white px-2 text-[12px] text-gray-900 outline-none focus:border-accent"
        />
        {err && <div className="mb-2 text-[11px] text-bear">{err}</div>}
        <button
          onClick={save}
          disabled={saving}
          className="h-8 w-full rounded-md bg-accent text-[12px] font-medium text-white disabled:opacity-50"
        >
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </div>
  )
}

/** AI 基金分析对话框 — 流式 NDJSON，与股票分析协议一致 */
function FundAnalysisDialog({
  thscode,
  name,
  onClose,
}: {
  thscode: string
  name: string
  onClose: () => void
}) {
  const [content, setContent] = useState('')
  const [summary, setSummary] = useState('')
  const [phase, setPhase] = useState<'loading' | 'streaming' | 'done' | 'error'>('loading')
  const [error, setError] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let alive = true
    setContent('')
    setSummary('')
    setError('')
    setPhase('loading')
    ;(async () => {
      try {
        for await (const ev of fundApi.analyzeStream(thscode)) {
          if (!alive) return
          if (ev.type === 'meta') {
            setSummary(ev.summary ?? '')
            setPhase('streaming')
          } else if (ev.type === 'delta') {
            setContent((c) => c + (ev.content ?? ''))
            setPhase('streaming')
          } else if (ev.type === 'error') {
            setError(ev.message ?? '分析失败')
            setPhase('error')
            return
          } else if (ev.type === 'done') {
            setPhase('done')
            return
          }
        }
        if (alive) setPhase('done')
      } catch (e) {
        if (alive) {
          setError(e instanceof Error ? e.message : '分析失败')
          setPhase('error')
        }
      }
    })()
    return () => {
      alive = false
    }
  }, [thscode])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [content])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-lg border border-border bg-[#0f1115] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border/40 px-4 py-2.5">
          <Sparkles className="h-4 w-4 text-accent" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-foreground">AI 基金分析</div>
            <div className="truncate text-[11px] text-muted">
              {name} <span className="font-mono">{thscode}</span>
            </div>
          </div>
          <button onClick={onClose} className="rounded p-1 text-muted hover:bg-elevated hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>
        {summary && (
          <div className="border-b border-border/40 bg-elevated/30 px-4 py-1.5 text-[11px] text-muted">
            {summary}
          </div>
        )}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3">
          {phase === 'loading' && (
            <div className="flex items-center gap-2 py-8 text-[12px] text-muted">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在分析基金数据…
            </div>
          )}
          {phase === 'error' && <div className="py-4 text-[12px] text-bear">{error}</div>}
          {(phase === 'streaming' || phase === 'done') && (
            <MarkdownRenderer content={content || '正在生成…'} />
          )}
        </div>
      </div>
    </div>
  )
}

function FundDetail({ item, onBack, researchHorizon }: { item: FundWatchItem | FundSearchItem; onBack: () => void; researchHorizon: string }) {
  const thscode = item.thscode
  const tradable = isTradable('kind_label' in item ? item.kind_label : '')
  const [quote, setQuote] = useState<FundQuote | null>(null)
  const [kline, setKline] = useState<{ adjusted: string; bars: { date: string; open: number | null; high: number | null; low: number | null; close: number | null; volume: number | null }[] } | null>(null)
  const [nav, setNav] = useState<FundNavPoint[] | null>(null)
  const [navSource, setNavSource] = useState<FundDataSource | null>(null)
  const [navLoading, setNavLoading] = useState(true)
  const [profile, setProfile] = useState<FundProfile | null>(null)
  const [err, setErr] = useState('')
  const [showAnalysis, setShowAnalysis] = useState(false)
  const [showAddPosition, setShowAddPosition] = useState(false)
  const [estimateData, setEstimateData] = useState<{
    prev_nav: number | null
    nav_date: string | null
    estimate: FundEstimate
    curve: FundEstimateCurve
    holdings: { items: HoldingItem[]; report_note: string }
  } | null>(null)

  useEffect(() => {
    let alive = true
    setErr('')
    setQuote(null)
    setKline(null)
    setNav(null)
    setNavSource(null)
    setNavLoading(true)
    setProfile(null)
    setEstimateData(null)
    fundApi
      .profile(thscode)
      .then((r) => alive && setProfile(r.profile))
      .catch(() => {})
    if (tradable) {
      fundApi
        .quote(thscode)
        .then((r) => alive && setQuote(r.quote))
        .catch((e) => alive && setErr(e instanceof Error ? e.message : '行情加载失败'))
      fundApi
        .kline(thscode, 250)
        .then((r) => alive && setKline(r))
        .catch(() => {})
    } else {
      fundApi
        .nav(thscode, 'year')
        .then((r) => {
          if (alive) {
            setNav(r.nav)
            setNavSource(r.source ?? null)
          }
        })
        .catch((e) => alive && setErr(e instanceof Error ? e.message : '净值加载失败'))
        .finally(() => alive && setNavLoading(false))
      fundApi
        .estimate(thscode)
        .then((r) => alive && setEstimateData(r))
        .catch(() => {})
    }
    return () => {
      alive = false
    }
  }, [thscode, tradable])

  // ETF/LOF 轮询实时快照
  useEffect(() => {
    if (!tradable) return
    const t = setInterval(async () => {
      try {
        const r = await fundApi.quote(thscode)
        setQuote(r.quote)
      } catch {
        /* 轮询失败静默, 保留上次数据 */
      }
    }, QUOTE_POLL_MS)
    return () => clearInterval(t)
  }, [thscode, tradable])

  const ohlc = useMemo(
    () =>
      (kline?.bars ?? [])
        .filter((b) => b.open !== null && b.high !== null && b.low !== null && b.close !== null)
        .map((b) => ({
          date: b.date,
          open: b.open as number,
          high: b.high as number,
          low: b.low as number,
          close: b.close as number,
          volume: b.volume ?? undefined,
        })),
    [kline],
  )

  return (
    <div className="space-y-2.5">
      <button
        onClick={onBack}
        className="flex items-center gap-1 text-[11px] text-muted hover:text-foreground"
      >
        <ArrowLeft className="h-3 w-3" />
        返回基金中心
      </button>
      <div className="flex items-center gap-1.5">
        <h2 className="text-xs font-semibold text-foreground">{item.name ?? thscode}</h2>
        <span className="rounded bg-elevated px-1.5 py-px font-mono text-[10px] text-secondary">
          {thscode}
        </span>
        <button
          onClick={() => setShowAnalysis(true)}
          className="ml-auto flex items-center gap-1 rounded-md bg-accent/15 px-2 py-1 text-[11px] font-medium text-accent hover:bg-accent/25"
        >
          <Sparkles className="h-3 w-3" />
          AI 分析
        </button>
        <button
          onClick={() => setShowAddPosition(true)}
          className="flex items-center gap-1 rounded-md border border-border/40 px-2 py-1 text-[11px] font-medium hover:border-accent hover:text-accent"
        >
          <Plus className="h-3 w-3" />
          加入持仓
        </button>
      </div>
      {showAddPosition && (
        <AddPositionDialog
          thscode={thscode}
          name={item.name ?? thscode}
          onClose={() => setShowAddPosition(false)}
          onSaved={() => setShowAddPosition(false)}
        />
      )}
      {showAnalysis && (
        <FundAnalysisDialog
          thscode={thscode}
          name={item.name ?? thscode}
          onClose={() => setShowAnalysis(false)}
        />
      )}
      {err && <div role="alert" className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{err} <Link className="underline" to="/settings?tab=data-sources">查看数据源设置</Link></div>}
      {profile && <ProfileBlock profile={profile} />}
      {tradable && quote && <QuoteCard quote={quote} />}
      {tradable && kline && (
        <Card>
          <div className="mb-1.5 flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <span className="h-3 w-0.5 rounded-full bg-gradient-to-b from-accent to-accent/30" />
              <BarChart3 className="h-3.5 w-3.5 text-accent" />
              <h2 className="text-xs font-semibold text-foreground">日K</h2>
            </div>
            <span className="text-[10px] text-muted">前复权口径 (数据源仅提供前复权)</span>
          </div>
          {ohlc.length > 0 ? (
            <EChartsCandlestick data={ohlc} height={300} showMA showInfoBar={false} showMarkers={false} />
          ) : (
            <div className="py-6 text-center text-[11px] text-muted">暂无K线数据</div>
          )}
        </Card>
      )}
      {!tradable && estimateData && (
        <EstimateCard
          estimate={estimateData.estimate}
          curve={estimateData.curve}
          prevNav={estimateData.prev_nav}
          navDate={estimateData.nav_date}
          holdings={estimateData.holdings}
        />
      )}
      {!tradable && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-2"><SectionTitle icon={LineChart} title="净值走势（近一年）" /><span className="text-xs text-muted">{navSource === 'eastmoney' ? '东方财富公开净值（单位净值，未复权）' : navSource === 'akshare' ? '东方财富 · AKShare' : navSource === 'fuyao' ? '扶摇' : '数据源待确认'}{nav?.length ? ` · 更新至 ${nav[nav.length - 1].nav_date}` : ''}</span></div>
          {nav && nav.length > 0 ? (
            <NavChart nav={nav} />
          ) : (
            <div className="py-6 text-center text-sm text-muted">
              {navLoading ? '加载中…' : err ? '净值暂不可用' : '暂无净值数据'}
            </div>
          )}
        </Card>
      )}
      {!tradable && <FundResearchPanel thscode={thscode} horizon={researchHorizon} />}
    </div>
  )
}

export function FundPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { items, loading, error, refresh } = useWatchlist()
  const portfolio = usePortfolio()
  const [q, setQ] = useState('')
  const [results, setResults] = useState<FundSearchItem[]>([])
  const [searching, setSearching] = useState(false)
  const [searched, setSearched] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [selected, setSelected] = useState<FundWatchItem | FundSearchItem | null>(() => {
    const code = searchParams.get('code')?.toUpperCase() ?? ''
    if (!/^\d{6}\.(OF|SH|SZ)$/.test(code)) return null
    return {
      thscode: code,
      ticker: code.split('.')[0],
      name: searchParams.get('name') || code,
      asset_type: code.endsWith('.OF') ? 'fund-otc' : 'fund-etf',
      kind_label: code.endsWith('.OF') ? '场外基金' : 'ETF',
    }
  })
  const openFund = useCallback((item: FundWatchItem | FundSearchItem) => {
    setSelected(item)
    setSearchParams({ code: item.thscode, name: item.name || item.thscode })
  }, [setSearchParams])
  const closeFund = useCallback(() => {
    setSelected(null)
    setSearchParams({})
  }, [setSearchParams])

  const doSearch = useCallback(async () => {
    const kw = q.trim()
    if (!kw) {
      setResults([])
      setSearched(false)
      setSearchError('')
      return
    }
    setSearching(true)
    setSearched(true)
    setSearchError('')
    try {
      const r = await fundApi.search(kw)
      setResults(r.items)
    } catch (cause) {
      setResults([])
      setSearchError(cause instanceof Error ? cause.message : '基金检索暂不可用')
    } finally {
      setSearching(false)
    }
  }, [q])

  const addWatch = useCallback(
    async (it: FundSearchItem) => {
      await fundApi.addWatch({ thscode: it.thscode, name: it.name, asset_type: it.asset_type })
      refresh()
    },
    [refresh],
  )

  const inWatch = useMemo(() => new Set(items.map((w) => w.thscode)), [items])
  const etfItems = items.filter((w) => isTradable(w.kind_label))
  const otcItems = items.filter((w) => !isTradable(w.kind_label))
  const [tab, setTab] = useState<'watch' | 'screener'>('watch')
  const requestedResearchHorizon = searchParams.get('research_horizon') ?? '1y'
  const researchHorizon = ['1m', '3m', '6m', '1y', '2y', '3y'].includes(requestedResearchHorizon) ? requestedResearchHorizon : '1y'

  if (selected) {
    return (
      <><PageHeader title="基金详情" className="pl-14 sm:pl-5" subtitle={selected.name || selected.thscode} right={<Link to="/ai-fund-screener" className="inline-flex items-center gap-1.5 rounded-btn border border-accent/35 bg-accent/10 px-3 py-1.5 text-xs text-accent"><Sparkles className="h-3.5 w-3.5" />AI 选基</Link>} /><div className="space-y-4 px-5 py-5 md:px-8"><FundDetail item={selected} onBack={closeFund} researchHorizon={researchHorizon} /></div></>
    )
  }

  return (
    <><PageHeader title="基金中心" className="pl-14 sm:pl-5" subtitle={<span className="hidden sm:inline">自选与持仓 · 净值与行情 · 历史业绩筛选</span>} right={<Link to="/ai-fund-screener" className="inline-flex items-center gap-1.5 rounded-btn border border-accent/35 bg-accent/10 px-3 py-1.5 text-xs text-accent"><Sparkles className="h-3.5 w-3.5" />AI 选基</Link>} /><div className="space-y-4 px-5 py-5 md:px-8">
      <div className="flex items-center justify-between gap-3">
        <div className="flex gap-1 rounded-lg border border-border bg-surface p-1">
          <button
            onClick={() => setTab('watch')}
            className={`min-h-9 rounded-md px-3 py-1 text-sm ${tab === 'watch' ? 'bg-accent text-white' : 'text-secondary hover:text-foreground'}`}
          >
            自选与持仓
          </button>
          <button
            onClick={() => setTab('screener')}
            className={`min-h-9 rounded-md px-3 py-1 text-sm ${tab === 'screener' ? 'bg-accent text-white' : 'text-secondary hover:text-foreground'}`}
          >
            历史业绩筛选
          </button>
        </div>
      </div>

      {tab === 'screener' ? (
        <FundScreener
          onSelect={(code, name) => openFund({ thscode: `${code}.OF`, ticker: code, name, asset_type: 'fund-otc', kind_label: '场外基金' })}
        />
      ) : (
        <>
      {/* 搜索 */}
      <Card>
        <div className="flex gap-1.5">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doSearch()}
              placeholder="搜索基金/ETF: 代码或名称, 如 005827 / 沪深300"
              className="h-10 w-full rounded-md border border-border bg-base pl-9 pr-3 text-sm text-foreground placeholder:text-muted outline-none focus:border-accent"
            />
          </div>
          <button
            onClick={doSearch}
            disabled={searching}
            className="h-10 shrink-0 rounded-md bg-accent px-4 text-sm font-medium text-white disabled:opacity-50"
          >
            {searching ? '搜索中…' : '搜索'}
          </button>
        </div>
        {searchError && <div role="alert" className="mt-3 rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{searchError}</div>}
        {searched && !searching && !searchError && results.length === 0 && (
          <div className="mt-1.5 text-[11px] text-muted">
            未找到相关基金，换个关键词或直接搜代码试试（如 510300）。
          </div>
        )}
        {results.length > 0 && (
          <ul className="mt-1.5 divide-y divide-border/40">
            {results.map((r) => (
              <li key={r.thscode} className="flex items-center gap-2 py-1.5">
                <button
                  onClick={() => openFund(r)}
                  className="flex flex-1 items-center gap-1.5 text-left min-w-0"
                >
                  <span className="text-[11px] font-medium hover:text-accent break-words">{r.name ?? r.thscode}</span>
                  <span className="font-mono text-[10px] text-muted">{r.thscode}</span>
                  <span className="rounded bg-elevated px-1 py-px text-[10px] text-secondary">
                    {r.kind_label}
                  </span>
                </button>
                {inWatch.has(r.thscode) ? (
                  <span className="text-[10px] text-muted">已在自选</span>
                ) : (
                  <button
                    onClick={() => addWatch(r)}
                    className="flex items-center gap-0.5 rounded-md border border-border/40 px-1.5 py-0.5 text-[10px] hover:border-accent hover:text-accent"
                  >
                    <Plus className="h-3 w-3" />
                    自选
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* 自选 */}
      <Card>
        <SectionTitle icon={Star} title="我的自选" />
        {loading && <div className="text-[11px] text-muted">加载中…</div>}
        {error && <div className="text-[11px] text-bear">{error}</div>}
        {!loading && items.length === 0 && (
          <div className="text-[11px] text-muted">
            还没有自选基金, 在上方搜索添加。场外基金看净值走势, ETF/LOF 看实时行情与日K。
          </div>
        )}
        {etfItems.length > 0 && (
          <>
            <div className="mb-1 text-[10px] text-muted">ETF / LOF</div>
            <ul className="mb-1.5 divide-y divide-border/40">
              {etfItems.map((w) => (
                <WatchRow key={w.thscode} item={w} onOpen={openFund} onRemove={async () => { await fundApi.removeWatch(w.thscode); refresh() }} />
              ))}
            </ul>
          </>
        )}
        {otcItems.length > 0 && (
          <>
            <div className="mb-1 text-[10px] text-muted">场外基金</div>
            <ul className="divide-y divide-border/40">
              {otcItems.map((w) => (
                <WatchRow key={w.thscode} item={w} onOpen={openFund} onRemove={async () => { await fundApi.removeWatch(w.thscode); refresh() }} />
              ))}
            </ul>
          </>
        )}
      </Card>

      {/* 持仓 */}
      <PortfolioSection
        items={portfolio.items}
        onOpen={(it) => openFund({ thscode: it.thscode, ticker: null, name: it.name ?? it.thscode, asset_type: 'fund-otc', kind_label: '场外基金' })}
        onRemove={async (thscode) => {
          await fundApi.removePosition(thscode)
          portfolio.refresh()
        }}
      />

      <p className="text-xs leading-5 text-muted">
        场外基金净值优先读取扶摇，未配置或暂不可用时使用公开净值来源；页面标注具体数据来源、单位净值口径和更新日期。ETF 行情和穿透估值依赖扶摇对应接口。
      </p>
        </>
      )}
    </div></>
  )
}

/** 我的持仓: 显示持仓金额、持有收益, 支持删除。添加从基金详情页的"加入持仓"进入。 */
function PortfolioSection({
  items,
  onOpen,
  onRemove,
}: {
  items: FundPortfolioItem[]
  onOpen: (i: FundPortfolioItem) => void
  onRemove: (thscode: string) => void
}) {
  const totalAmount = items.reduce((s, it) => s + (it.amount || 0), 0)
  const totalProfit = items.reduce((s, it) => s + (it.profit || 0), 0)
  const totalUp = totalProfit >= 0
  return (
    <Card>
      <SectionTitle icon={BarChart3} title="我的持仓" />
      {items.length === 0 ? (
        <div className="text-[11px] text-muted">
          还没有持仓。在基金详情页点"加入持仓"并填写金额即可跟踪。
        </div>
      ) : (
        <>
          <div className="mb-1.5 flex items-baseline gap-3">
            <span className="text-[11px] text-muted">总资产</span>
            <span className="font-mono text-base font-bold tabular-nums">
              ¥{totalAmount.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
            <span className={cn('font-mono text-[11px] tabular-nums', totalUp ? 'text-bull' : 'text-bear')}>
              {totalUp ? '+' : ''}{totalProfit.toFixed(2)}
            </span>
          </div>
          <ul className="divide-y divide-border/40">
            {items.map((it) => {
              const up = (it.profit || 0) >= 0
              const cost = (it.amount || 0) - (it.profit || 0)
              const pct = cost > 0 ? (it.profit / cost) * 100 : 0
              return (
                <li key={it.thscode} className="flex items-center gap-2 py-1.5">
                  <button onClick={() => onOpen(it)} className="flex flex-1 items-center gap-2 text-left min-w-0">
                    <span className="text-[11px] font-medium hover:text-accent truncate">{it.name ?? it.thscode}</span>
                    <span className="font-mono text-[10px] text-muted shrink-0">{it.thscode}</span>
                  </button>
                  <span className="font-mono text-[11px] tabular-nums shrink-0">
                    ¥{(it.amount || 0).toFixed(2)}
                  </span>
                  <span className={cn('font-mono text-[11px] tabular-nums shrink-0', up ? 'text-bull' : 'text-bear')}>
                    {up ? '+' : ''}{(it.profit || 0).toFixed(2)} ({up ? '+' : ''}{pct.toFixed(2)}%)
                  </span>
                  <button onClick={() => onRemove(it.thscode)} className="text-[10px] text-muted hover:text-bear shrink-0">
                    移除
                  </button>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </Card>
  )
}

function WatchRow({
  item,
  onOpen,
  onRemove,
}: {
  item: FundWatchItem
  onOpen: (i: FundWatchItem) => void
  onRemove: () => void
}) {
  const [quote, setQuote] = useState<FundQuote | null>(null)
  const tradable = isTradable(item.kind_label)

  useEffect(() => {
    if (!tradable) return
    let alive = true
    fundApi
      .quote(item.thscode)
      .then((r) => alive && setQuote(r.quote))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [item.thscode, tradable])

  const up = (quote?.change_pct ?? 0) >= 0
  return (
    <li className="flex items-center gap-2 py-1.5">
      <button onClick={() => onOpen(item)} className="flex flex-1 items-center gap-2 text-left">
        <span className="text-[11px] font-medium hover:text-accent">{item.name ?? item.thscode}</span>
        <span className="font-mono text-[10px] text-muted">{item.thscode}</span>
        {tradable && quote && (
          <>
            <span className="font-mono text-[11px] tabular-nums">{quote.last_price?.toFixed(3) ?? '—'}</span>
            <span className={cn('font-mono text-[11px] tabular-nums', up ? 'text-bull' : 'text-bear')}>
              {pctText(quote.change_pct)}
            </span>
          </>
        )}
      </button>
      <button onClick={onRemove} className="text-[10px] text-muted hover:text-bear">
        移除
      </button>
    </li>
  )
}
