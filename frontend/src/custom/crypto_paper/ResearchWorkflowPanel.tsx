import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import * as echarts from 'echarts'
import { Activity, BarChart3, BookOpen, Loader2, RefreshCcw, Sparkles } from 'lucide-react'
import {
  cryptoApi,
  type CryptoResearchBacktestRequest,
  type CryptoResearchBacktestRun,
  type CryptoResearchBacktestSummary,
  type CryptoResearchDecision,
  type CryptoResearchNavPoint,
  type CryptoResearchReview,
  type CryptoStrategyAccount,
  type CryptoStrategyId,
  type CryptoStrategyInterval,
  type CryptoSymbol,
} from './client'
import { describeStrategyParameters, STRATEGY_FIELDS, STRATEGY_NAMES, readStrategyParameters } from './strategy-research'

const SYMBOLS: CryptoSymbol[] = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
const INTERVALS: CryptoStrategyInterval[] = ['1h', '4h']
const STRATEGY_OPTIONS = (Object.keys(STRATEGY_NAMES) as CryptoStrategyId[]).map(id => ({ id, name: STRATEGY_NAMES[id] }))
const DEFAULT_BACKTEST = {
  strategy_id: 'ema_trend' as CryptoStrategyId,
  symbol: 'BTCUSDT' as CryptoSymbol,
  interval: '1h' as CryptoStrategyInterval,
  bars: '500',
  initial_cash: '10000',
  allocation_pct: '10',
  stop_loss_pct: '2',
  take_profit_pct: '4',
}

type ResearchTab = 'backtest' | 'decisions' | 'reviews'
type DecisionResult = { accountId: string; decisions: CryptoResearchDecision[] }

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

function fmtNumber(value: string | number | null | undefined, digits = 2) {
  if (value == null || value === '') return '—'
  const number = Number(value)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '—'
}

function fmtPercent(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value.toFixed(2)}%`
}

function fmtReturn(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function fmtDate(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

function strategyName(id: CryptoStrategyId) {
  return STRATEGY_NAMES[id] ?? id
}

function tradeLabel(value: string | undefined) {
  const labels: Record<string, string> = {
    open_long: '开多', open_short: '开空', close_long: '平多', close_short: '平空',
    strategy_signal: '策略信号', signal_flat: '空仓信号', signal_reversal: '反向信号',
    stop_loss_gap: '跳空止损', take_profit_gap: '跳空止盈', stop_loss: '区间止损估算',
    take_profit: '区间止盈估算', end_of_test_estimate: '期末估算平仓', isolated_liquidation: '研究强平',
    waiting: '等待', blocked: '拦截', executed: '已执行', risk_exit: '风险退出', paused: '已暂停',
  }
  return value ? labels[value] ?? value : '—'
}

function paramsLabel(run: CryptoResearchBacktestSummary) {
  const params = run.config.strategy_params ?? {}
  return `${strategyName(run.config.strategy_id)} · ${describeStrategyParameters(run.config.strategy_id, params)} · 本金 ${fmtNumber(run.config.initial_cash)} / 仓位 ${run.config.allocation_pct}% / 止损 ${run.config.stop_loss_pct}% / 止盈 ${run.config.take_profit_pct}%`
}

function sameSample(a: CryptoResearchBacktestSummary, b: CryptoResearchBacktestSummary) {
  return a.config.symbol === b.config.symbol && a.config.interval === b.config.interval
    && Boolean(a.evidence.bars_sha256) && a.evidence.bars_sha256 === b.evidence.bars_sha256
    && Number(a.evidence.fee_rate) === Number(b.evidence.fee_rate) && a.evidence.slippage_bps === b.evidence.slippage_bps
    && a.evidence.bars_count === b.evidence.bars_count
    && a.evidence.data_start_ms === b.evidence.data_start_ms
    && a.evidence.data_end_ms === b.evidence.data_end_ms
}

function decisionTarget(target: string | null) {
  if (target === 'long') return '多向候选'
  if (target === 'short') return '空向候选'
  if (target === 'flat') return '空仓'
  if (target === 'hold') return '保持当前状态'
  return target ?? '—'
}

function candidateText(candidate: unknown, index: number) {
  if (typeof candidate === 'string' && candidate.trim()) return candidate
  if (typeof candidate === 'number' || typeof candidate === 'boolean') return String(candidate)
  if (candidate && typeof candidate === 'object' && !Array.isArray(candidate)) {
    const fields = candidate as Record<string, unknown>
    const label = [fields.name, fields.symbol, fields.strategy_name, fields.strategy_id]
      .find(value => typeof value === 'string' && value.trim())
    const reason = [fields.reason, fields.summary, fields.last_reason]
      .find(value => typeof value === 'string' && value.trim())
    if (typeof label === 'string' && typeof reason === 'string') return `${label} · ${reason}`
    if (typeof label === 'string') return label
    if (typeof reason === 'string') return reason
  }
  return `候选记录 ${index + 1}`
}

function ResearchEquityChart({ nav }: { nav: CryptoResearchNavPoint[] }) {
  const chartRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!chartRef.current || nav.length === 0) return
    const points = nav.map(point => ({ at: point.at_ms, value: Number(point.equity) }))
    if (!points.some(point => Number.isFinite(point.value))) return
    const chart = echarts.init(chartRef.current, undefined, { renderer: 'canvas' })
    const render = () => {
      const dark = document.documentElement.classList.contains('dark')
      const text = dark ? '#8E8E96' : '#52525B'
      const line = dark ? '#353539' : '#E4E4E7'
      chart.setOption({
        animation: false,
        backgroundColor: 'transparent',
        grid: { left: 12, right: 16, top: 18, bottom: 30, containLabel: true },
        tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => fmtNumber(typeof value === 'number' ? value : null, 4) },
        xAxis: {
          type: 'category',
          boundaryGap: false,
          data: points.map(point => new Date(point.at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })),
          axisLine: { lineStyle: { color: line } }, axisLabel: { color: text, fontSize: 10, hideOverlap: true },
        },
        yAxis: {
          type: 'value', scale: true,
          axisLabel: { color: text, fontSize: 10, formatter: (value: number) => fmtNumber(value) },
          splitLine: { lineStyle: { color: line } },
        },
        series: [{
          name: '研究权益', type: 'line', data: points.map(point => Number.isFinite(point.value) ? point.value : null),
          showSymbol: points.length === 1, symbolSize: 5, connectNulls: false,
          lineStyle: { color: '#8B5CF6', width: 2 }, itemStyle: { color: '#8B5CF6' },
          areaStyle: { color: 'rgba(139,92,246,.08)' },
        }],
      }, true)
    }
    render()
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => chart.resize())
    if (resize && chartRef.current) resize.observe(chartRef.current)
    const theme = new MutationObserver(render)
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => { resize?.disconnect(); theme.disconnect(); chart.dispose() }
  }, [nav])
  if (!nav.length) return <div className="flex h-40 items-center justify-center rounded-btn border border-border text-xs text-muted">没有净值曲线数据</div>
  return <div ref={chartRef} className="h-48 w-full" role="img" aria-label="研究回测权益曲线" />
}

function ResultMetrics({ run }: { run: CryptoResearchBacktestRun }) {
  const metrics = run.metrics
  const items = [
    ['区间回报', fmtReturn(metrics.return_pct)],
    ['最大回撤', fmtPercent(metrics.max_drawdown_pct)],
    ['已平仓交易', String(metrics.closed_trades)],
    ['胜率', metrics.win_rate_pct == null ? '—' : fmtPercent(metrics.win_rate_pct)],
    ['手续费', fmtNumber(metrics.fees_paid, 4)],
    ['期末权益', fmtNumber(metrics.ending_equity, 4)],
  ]
  return <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
    {items.map(([label, value]) => <div key={label} className="rounded-btn border border-border p-2 text-[11px]">
      <div className="text-muted">{label}</div><div className="mt-1 break-words font-mono text-foreground">{value}</div>
    </div>)}
  </div>
}

export function ResearchWorkflowPanel() {
  const [tab, setTab] = useState<ResearchTab>('backtest')
  const [form, setForm] = useState(DEFAULT_BACKTEST)
  const [strategyParams, setStrategyParams] = useState<Record<string, string>>(() => Object.fromEntries(
    STRATEGY_FIELDS[DEFAULT_BACKTEST.strategy_id].map(field => [field.key, String(field.defaultValue)]),
  ))
  const [formError, setFormError] = useState('')
  const [backtestError, setBacktestError] = useState('')
  const [backtestPending, setBacktestPending] = useState(false)
  const [backtestNotice, setBacktestNotice] = useState('')
  const [history, setHistory] = useState<CryptoResearchBacktestSummary[] | null>(null)
  const [historyError, setHistoryError] = useState('')
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyVersion, setHistoryVersion] = useState(0)
  const [selectedRunId, setSelectedRunId] = useState('')
  const [run, setRun] = useState<CryptoResearchBacktestRun | null>(null)
  const [runLoadingId, setRunLoadingId] = useState('')
  const [runError, setRunError] = useState('')
  const runGeneration = useRef(0)

  const [accounts, setAccounts] = useState<CryptoStrategyAccount[] | null>(null)
  const [accountsLoading, setAccountsLoading] = useState(false)
  const [accountsError, setAccountsError] = useState('')
  const [accountId, setAccountId] = useState('')
  const [decisionResult, setDecisionResult] = useState<DecisionResult | null>(null)
  const [decisionLoading, setDecisionLoading] = useState(false)
  const [decisionError, setDecisionError] = useState('')
  const decisionGeneration = useRef(0)

  const [reviews, setReviews] = useState<CryptoResearchReview[] | null>(null)
  const [reviewsLoading, setReviewsLoading] = useState(false)
  const [reviewsError, setReviewsError] = useState('')
  const [reviewsVersion, setReviewsVersion] = useState(0)
  const [reviewPending, setReviewPending] = useState(false)
  const [reviewNotice, setReviewNotice] = useState('')
  const [reviewError, setReviewError] = useState('')
  const [aiPending, setAiPending] = useState(false)
  const [aiNotice, setAiNotice] = useState('')
  const [aiError, setAiError] = useState('')

  const activeFields = STRATEGY_FIELDS[form.strategy_id]
  const eligibleAccounts = (accounts ?? []).filter(account => account.exchange === 'bitget'
    && account.market === 'usdm' && account.effective_readonly !== true)
  const displayedDecisions = decisionResult?.accountId === accountId ? decisionResult.decisions : null
  const latestReview = reviews?.[0] ?? null
  const recentComparisons = useMemo(() => [...(history ?? [])]
    .sort((a, b) => b.created_at_ms - a.created_at_ms).slice(0, 2), [history])

  useEffect(() => {
    let alive = true
    setHistoryLoading(true)
    void cryptoApi.researchHistory().then(result => {
      if (alive) {
        const sorted = [...result.runs].sort((a, b) => b.created_at_ms - a.created_at_ms)
        setHistory(sorted)
        if (!selectedRunId && sorted[0]) setSelectedRunId(sorted[0].id)
        setHistoryError('')
      }
    }).catch(error => { if (alive) setHistoryError(errorMessage(error, '研究回测历史读取失败')) })
      .finally(() => { if (alive) setHistoryLoading(false) })
    return () => { alive = false }
  }, [historyVersion])

  useEffect(() => {
    if (tab !== 'decisions' || accounts !== null) return
    let alive = true
    setAccountsLoading(true)
    setAccountsError('')
    void cryptoApi.strategyAccounts().then(result => {
      if (!alive) return
      const list = result.accounts.filter(account => account.exchange === 'bitget'
        && account.market === 'usdm' && account.effective_readonly !== true)
      setAccounts(list)
      setAccountId(current => list.some(account => account.id === current) ? current : list[0]?.id ?? '')
    }).catch(error => { if (alive) setAccountsError(errorMessage(error, '策略账户列表读取失败')) })
      .finally(() => { if (alive) setAccountsLoading(false) })
    return () => { alive = false }
  }, [tab, accounts])

  useEffect(() => {
    if (tab !== 'reviews') return
    let alive = true
    setReviewsLoading(true)
    setReviewsError('')
    void cryptoApi.researchReviews().then(result => {
      if (alive) setReviews([...result.reviews].sort((a, b) => b.created_at_ms - a.created_at_ms))
    }).catch(error => { if (alive) setReviewsError(errorMessage(error, '每日复盘读取失败')) })
      .finally(() => { if (alive) setReviewsLoading(false) })
    return () => { alive = false }
  }, [tab, reviewsVersion])

  useEffect(() => {
    setStrategyParams(Object.fromEntries(activeFields.map(field => [field.key, String(field.defaultValue)])))
    setFormError('')
  }, [activeFields])

  const changeStrategy = (strategy_id: CryptoStrategyId) => setForm(current => ({ ...current, strategy_id }))

  const submitBacktest = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setFormError('')
    setBacktestError('')
    setBacktestNotice('')
    let body: CryptoResearchBacktestRequest
    try {
      const bars = Number(form.bars)
      const initial_cash = Number(form.initial_cash)
      const allocation_pct = Number(form.allocation_pct)
      const stop_loss_pct = Number(form.stop_loss_pct)
      const take_profit_pct = Number(form.take_profit_pct)
      if (!Number.isInteger(bars) || bars < 250 || bars > 1000) throw new Error('样本 K 线数须为 250 至 1000 的整数。')
      if (!Number.isFinite(initial_cash) || initial_cash < 100 || initial_cash > 10000000) throw new Error('初始资金须为 100 至 1000 万 USDT。')
      if (!Number.isFinite(allocation_pct) || allocation_pct <= 0 || allocation_pct > 80) throw new Error('单次仓位须大于 0 且不超过 80%。')
      if (!Number.isFinite(stop_loss_pct) || stop_loss_pct < 0.1 || stop_loss_pct > 50) throw new Error('止损须为 0.1% 至 50%。')
      if (!Number.isFinite(take_profit_pct) || take_profit_pct < 0.1 || take_profit_pct > 100) throw new Error('止盈须为 0.1% 至 100%。')
      body = {
        strategy_id: form.strategy_id,
        strategy_params: activeFields.length ? readStrategyParameters(form.strategy_id, strategyParams) : null,
        symbol: form.symbol,
        interval: form.interval,
        bars,
        initial_cash,
        allocation_pct,
        stop_loss_pct,
        take_profit_pct,
      }
    } catch (error) {
      setFormError(errorMessage(error, '请检查回测参数。'))
      return
    }
    setBacktestPending(true)
    try {
      const result = await cryptoApi.researchBacktest(body)
      setRun(result)
      setSelectedRunId(result.id)
      setRunError('')
      setBacktestNotice('回测已保存为研究记录；没有创建、启用或修改策略账户。')
      setHistory(current => {
        const summary: CryptoResearchBacktestSummary = {
          id: result.id, created_at_ms: result.created_at_ms, config: result.config,
          evidence: result.evidence, metrics: result.metrics, warnings: result.warnings, method: result.method,
        }
        return [summary, ...(current ?? []).filter(item => item.id !== result.id)]
      })
      setHistoryError('')
    } catch (error) {
      setBacktestError(errorMessage(error, '研究回测失败。'))
    } finally {
      setBacktestPending(false)
    }
  }

  const loadRun = async (id: string) => {
    setSelectedRunId(id)
    setRunError('')
    if (run?.id === id) return
    const generation = ++runGeneration.current
    setRunLoadingId(id)
    try {
      const result = await cryptoApi.researchRun(id)
      if (runGeneration.current === generation) setRun(result)
    } catch (error) {
      if (runGeneration.current === generation) setRunError(errorMessage(error, '回测明细读取失败。'))
    } finally {
      if (runGeneration.current === generation) setRunLoadingId('')
    }
  }

  const loadDecisions = async () => {
    if (!accountId) return
    const generation = ++decisionGeneration.current
    setDecisionLoading(true)
    setDecisionError('')
    try {
      const result = await cryptoApi.researchAccountDecisions(accountId)
      if (decisionGeneration.current === generation) setDecisionResult({ accountId, decisions: result.decisions })
    } catch (error) {
      if (decisionGeneration.current === generation) setDecisionError(errorMessage(error, '决策记录读取失败。'))
    } finally {
      if (decisionGeneration.current === generation) setDecisionLoading(false)
    }
  }

  const createReview = async () => {
    setReviewPending(true)
    setReviewError('')
    setReviewNotice('')
    try {
      await cryptoApi.createResearchReview()
      setReviewNotice('今日确定性复盘已生成。统计基于去重决策记录和实际账本数据，不调用 AI。')
      setReviewsVersion(version => version + 1)
    } catch (error) {
      setReviewError(errorMessage(error, '生成每日复盘失败。'))
    } finally {
      setReviewPending(false)
    }
  }

  const interpretReview = async () => {
    setAiPending(true)
    setAiError('')
    setAiNotice('')
    try {
      const result = await cryptoApi.researchReviewAI()
      const review = { ...result.review, ai: result.ai }
      setReviews(current => [review, ...(current ?? []).filter(item => item.date !== review.date)]
        .sort((a, b) => b.created_at_ms - a.created_at_ms))
      setAiNotice('AI 解读已生成。')
    } catch (error) {
      setAiError(errorMessage(error, 'AI 复盘解读失败。'))
    } finally {
      setAiPending(false)
    }
  }

  const activeRun = run?.id === selectedRunId ? run : null
  return <section className="space-y-4 rounded-card border border-border bg-surface p-3 md:p-4" aria-label="策略研究工作流">
    <div className="flex flex-wrap items-start gap-3">
      <div className="min-w-0 w-full md:w-auto md:flex-1">
        <div className="flex items-center gap-2"><Activity className="h-4 w-4 text-accent" /><h2 className="text-sm font-semibold">策略研究工作流</h2></div>
        <p className="mt-1 max-w-4xl text-xs leading-relaxed text-muted">独立执行 1 倍历史样本研究、查看账户决策记录和每日复盘。回测只写入研究记录，不会创建或启用策略账户。</p>
      </div>
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="研究区块">
        {([
          ['backtest', '回测研究'], ['decisions', '决策记录'], ['reviews', '每日复盘'],
        ] as const).map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={tab === id}
          onClick={() => setTab(id)} className={`rounded-btn border px-3 py-2 text-xs ${tab === id ? 'border-accent bg-accent/10 text-accent' : 'border-border text-secondary'}`}>{label}</button>)}
      </div>
    </div>

    {tab === 'backtest' && <div className="space-y-4">
      <div className="rounded-btn border border-amber-500/30 bg-amber-500/5 p-3 text-xs leading-relaxed text-amber-300">
        固定 1 倍研究；取最近已收盘 K 线，信号在本根收盘后确认，按下一根 K 线开盘价模拟成交，并计入公开费率与 5 bps 滑点。回测回报为<strong>手续费后、资金费前</strong>，不含资金费，不等同完整净收益。它与本地策略账户按前向轮询取得报价成交、按轮询价检查止盈止损的模型不同；不同模型的结果不可直接视为实盘表现。
      </div>
      <form onSubmit={event => { void submitBacktest(event) }} className="rounded-btn border border-border p-3">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs text-muted">策略<select aria-label="研究策略" required value={form.strategy_id} onChange={event => changeStrategy(event.target.value as CryptoStrategyId)}
            className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground">
            {STRATEGY_OPTIONS.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select></label>
          <label className="text-xs text-muted">交易对<select aria-label="研究交易对" value={form.symbol} onChange={event => setForm(current => ({ ...current, symbol: event.target.value as CryptoSymbol }))}
            className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground">{SYMBOLS.map(symbol => <option key={symbol}>{symbol}</option>)}</select></label>
          <label className="text-xs text-muted">周期<select aria-label="研究周期" value={form.interval} onChange={event => setForm(current => ({ ...current, interval: event.target.value as CryptoStrategyInterval }))}
            className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground">{INTERVALS.map(interval => <option key={interval} value={interval}>{interval}</option>)}</select></label>
          <label className="text-xs text-muted">样本 K 线数<input aria-label="样本K线数" type="number" min="250" max="1000" step="1" required value={form.bars} onChange={event => setForm(current => ({ ...current, bars: event.target.value }))}
            className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
          {activeFields.map(field => <label key={field.key} className="text-xs text-muted">{field.label}<input aria-label={field.label} type="number" min={field.min} max={field.max} step="1" required value={strategyParams[field.key] ?? String(field.defaultValue)}
            onChange={event => setStrategyParams(current => ({ ...current, [field.key]: event.target.value }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>)}
          <label className="text-xs text-muted">初始资金（USDT）<input aria-label="研究初始资金" type="number" min="100" max="10000000" step="any" required value={form.initial_cash} onChange={event => setForm(current => ({ ...current, initial_cash: event.target.value }))}
            className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
          <label className="text-xs text-muted">单次仓位（%）<input aria-label="研究仓位百分比" type="number" min="0.000001" max="80" step="any" required value={form.allocation_pct} onChange={event => setForm(current => ({ ...current, allocation_pct: event.target.value }))}
            className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
          <label className="text-xs text-muted">止损（%）<input aria-label="研究止损百分比" type="number" min="0.1" max="50" step="any" required value={form.stop_loss_pct} onChange={event => setForm(current => ({ ...current, stop_loss_pct: event.target.value }))}
            className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
          <label className="text-xs text-muted">止盈（%）<input aria-label="研究止盈百分比" type="number" min="0.1" max="100" step="any" required value={form.take_profit_pct} onChange={event => setForm(current => ({ ...current, take_profit_pct: event.target.value }))}
            className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
        </div>
        {activeFields.length === 0 && <p className="mt-2 text-[11px] text-muted">所选策略没有可调参数，本次参数以 null 提交。</p>}
        {formError && <p role="alert" className="mt-2 text-xs text-danger">{formError}</p>}
        {backtestError && <p role="alert" className="mt-2 text-xs text-danger">{backtestError}</p>}
        {backtestNotice && <p role="status" className="mt-2 text-xs text-accent">{backtestNotice}</p>}
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="submit" disabled={backtestPending} className="inline-flex items-center gap-2 rounded-btn bg-accent px-3 py-2 text-xs font-medium text-white disabled:opacity-50">
            {backtestPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}{backtestPending ? '回测计算中…' : '运行研究回测'}
          </button>
          <span className="text-[11px] text-muted">样本最近 K 线；范围 250–1000 根，默认 500 根。参数和风控仅用于本次研究，不会改写账户设置。</span>
        </div>
      </form>

      <div className="space-y-3 rounded-btn border border-border p-3">
        <div className="flex flex-wrap items-center gap-2"><h3 className="mr-auto text-xs font-semibold">回测历史</h3>
          <button type="button" onClick={() => setHistoryVersion(version => version + 1)} disabled={historyLoading} className="inline-flex items-center gap-1 rounded-btn border border-border px-2.5 py-1.5 text-[11px] text-secondary disabled:opacity-50">
            <RefreshCcw className={`h-3 w-3 ${historyLoading ? 'animate-spin' : ''}`} />刷新
          </button>
        </div>
        {historyError && <p role="alert" className="text-xs text-danger">历史读取失败：{historyError}。已有结果仍保留。</p>}
        {historyLoading && history === null && <p className="py-4 text-center text-xs text-muted">正在读取回测历史…</p>}
        {!historyLoading && history !== null && history.length === 0 && <p className="py-4 text-center text-xs text-muted">暂无研究回测记录。运行一次回测后，结果会保存在研究历史中。</p>}
        {history && history.length > 0 && <div className="overflow-x-auto rounded-btn border border-border">
          <table className="w-full min-w-[760px] text-left text-[11px]">
            <thead className="border-b border-border bg-elevated/40 text-muted"><tr><th className="px-2 py-2">时间 / 策略</th><th>标的 / 周期 / 样本</th><th>回报 / 回撤</th><th>交易 / 胜率</th><th>操作</th></tr></thead>
            <tbody>{history.slice(0, 12).map(item => <tr key={item.id} className="border-b border-border/50 last:border-0">
              <td className="px-2 py-2"><div className="text-foreground">{fmtDate(item.created_at_ms)}</div><div className="mt-0.5 text-muted">{paramsLabel(item)}</div></td>
              <td className="py-2 text-secondary">{item.config.symbol} · {item.config.interval} · {item.evidence.bars_count} 根</td>
              <td className="py-2 font-mono text-secondary">{fmtReturn(item.metrics.return_pct)} / {fmtPercent(item.metrics.max_drawdown_pct)}</td>
              <td className="py-2 text-secondary">{item.metrics.closed_trades} 笔 · {item.metrics.win_rate_pct == null ? '—' : fmtPercent(item.metrics.win_rate_pct)}</td>
              <td className="py-2 pr-2"><button type="button" onClick={() => { void loadRun(item.id) }} disabled={runLoadingId === item.id} className="rounded-btn border border-border px-2 py-1 text-accent disabled:opacity-50">{runLoadingId === item.id ? '加载中…' : selectedRunId === item.id ? '当前结果' : '查看'}</button></td>
            </tr>)}</tbody>
          </table>
        </div>}
        {recentComparisons.length === 2 && <div className="space-y-2 rounded-btn border border-border p-3">
          <div className="flex flex-wrap items-center gap-2"><BarChart3 className="h-3.5 w-3.5 text-accent" /><h4 className="text-xs font-semibold">最近两次配置与指标对照</h4></div>
          <p className={`text-[11px] ${sameSample(recentComparisons[0], recentComparisons[1]) ? 'text-accent' : 'text-amber-300'}`}>
            {sameSample(recentComparisons[0], recentComparisons[1])
              ? '标的、周期和实际样本区间一致，可在相同样本口径下比较。'
              : '标的、周期或实际样本区间不同，结果不可直接横向比较。'}
          </p>
          <div className="overflow-x-auto"><table className="w-full min-w-[620px] text-left text-[11px]"><thead className="text-muted"><tr><th className="py-1">回测时间 / 参数</th><th>样本</th><th>回报</th><th>回撤</th><th>平仓 / 胜率</th></tr></thead><tbody>
            {recentComparisons.map(item => <tr key={item.id} className="border-t border-border/50"><td className="py-2 text-secondary">{fmtDate(item.created_at_ms)}<div className="text-muted">{paramsLabel(item)}</div></td><td className="text-secondary">{item.config.symbol} · {item.config.interval}<div className="text-muted">{item.evidence.bars_count} 根 · {fmtDate(item.evidence.data_start_ms)} — {fmtDate(item.evidence.data_end_ms)}</div></td><td className="font-mono text-secondary">{fmtReturn(item.metrics.return_pct)}</td><td className="font-mono text-secondary">{fmtPercent(item.metrics.max_drawdown_pct)}</td><td className="text-secondary">{item.metrics.closed_trades} · {item.metrics.win_rate_pct == null ? '—' : fmtPercent(item.metrics.win_rate_pct)}</td></tr>)}
          </tbody></table></div>
        </div>}
      </div>

      {runError && <p role="alert" className="rounded-btn border border-danger/40 bg-danger/10 p-3 text-xs text-danger">{runError}；其他回测记录仍可查看。</p>}
      {activeRun && <div className="space-y-3 rounded-btn border border-accent/30 bg-base/30 p-3">
        <div className="flex flex-wrap items-start gap-2"><div className="mr-auto"><h3 className="text-xs font-semibold">研究回测结果 · {paramsLabel(activeRun)}</h3><p className="mt-1 text-[11px] text-muted">{activeRun.config.symbol} · {activeRun.config.interval} · 实际 {activeRun.evidence.bars_count} 根已收盘 K 线 · {fmtDate(activeRun.evidence.data_start_ms)} — {fmtDate(activeRun.evidence.data_end_ms)}</p></div>
          <span className="rounded-full border border-amber-500/30 px-2 py-1 text-[10px] text-amber-300">手续费后 · 资金费前</span>
        </div>
        <ResultMetrics run={activeRun} />
        <ResearchEquityChart nav={activeRun.nav} />
        <p className="text-[11px] leading-relaxed text-muted">公开手续费率 {fmtNumber(Number(activeRun.evidence.fee_rate) * 100, 4)}% · 滑点 {activeRun.evidence.slippage_bps} bps · 交易所 {activeRun.evidence.exchange}。资金费未计入。</p>
        {activeRun.warnings.length > 0 && <div className="rounded-btn border border-amber-500/30 bg-amber-500/5 p-3 text-[11px] text-amber-300"><div className="font-semibold">研究限制与警告</div><ul className="mt-1 list-disc space-y-1 pl-4">{activeRun.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul></div>}
        <p className="text-[11px] leading-relaxed text-muted">{activeRun.method}</p>
        <div className="overflow-x-auto rounded-btn border border-border"><table className="w-full min-w-[760px] text-left text-[11px]"><thead className="border-b border-border bg-elevated/40 text-muted"><tr><th className="px-2 py-2">信号时间</th><th>模拟成交时间</th><th>动作</th><th>价格</th><th>数量</th><th>手续费</th><th>已实现盈亏</th></tr></thead><tbody>
          {activeRun.trades.length ? activeRun.trades.slice(-20).reverse().map((trade, index) => <tr key={`${trade.signal_time_ms}-${trade.action}-${index}`} className="border-b border-border/50 last:border-0"><td className="px-2 py-2 text-secondary">{fmtDate(trade.signal_time_ms)}</td><td className="text-secondary">{fmtDate(trade.fill_time_ms)}</td><td className="text-secondary">{tradeLabel(trade.action)}<div className="text-muted">{tradeLabel(trade.reason)}</div></td><td className="font-mono text-secondary">{fmtNumber(trade.price, 6)}</td><td className="font-mono text-secondary">{fmtNumber(trade.quantity, 6)}</td><td className="font-mono text-secondary">{fmtNumber(trade.fee, 6)}</td><td className="font-mono text-secondary">{fmtNumber(trade.realized_pnl, 6)}</td></tr>)
            : <tr><td colSpan={7} className="px-2 py-5 text-center text-muted">样本中没有模拟成交记录。</td></tr>}
        </tbody></table></div>
      </div>}
    </div>}

    {tab === 'decisions' && <div className="space-y-3">
      <div className="rounded-btn border border-border p-3">
        <div className="flex flex-wrap items-end gap-2">
          <label className="min-w-[220px] flex-1 text-xs text-muted">Bitget 策略账户<select aria-label="决策账户" value={accountId} onChange={event => setAccountId(event.target.value)} disabled={accountsLoading || eligibleAccounts.length === 0}
            className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground"><option value="">选择策略账户</option>{eligibleAccounts.map(account => <option key={account.id} value={account.id}>{account.name} · {account.symbol} · {account.interval}</option>)}</select></label>
          <button type="button" onClick={() => { void loadDecisions() }} disabled={!accountId || decisionLoading} className="inline-flex items-center gap-2 rounded-btn border border-accent/40 px-3 py-2 text-xs text-accent disabled:opacity-50">{decisionLoading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}加载决策记录</button>
        </div>
        <p className="mt-2 text-[11px] text-muted">记录仅供检查策略每次判断与风控结果，不修改账户配置，也不会触发新交易。</p>
      </div>
      {accountsLoading && <p role="status" className="py-5 text-center text-xs text-muted">正在读取策略账户…</p>}
      {accountsError && <p role="alert" className="rounded-btn border border-danger/40 bg-danger/10 p-3 text-xs text-danger">账户列表读取失败：{accountsError}</p>}
      {!accountsLoading && accounts !== null && eligibleAccounts.length === 0 && <p className="rounded-btn border border-border p-5 text-center text-xs text-muted">暂无可查看的 Bitget USDT 本位策略账户。</p>}
      {decisionError && <p role="alert" className="rounded-btn border border-danger/40 bg-danger/10 p-3 text-xs text-danger">决策记录读取失败：{decisionError}。已加载的当前账户记录仍保留。</p>}
      {decisionLoading && <p role="status" className="py-2 text-center text-xs text-muted">正在读取决策记录…</p>}
      {displayedDecisions && displayedDecisions.length === 0 && <p className="rounded-btn border border-border p-5 text-center text-xs text-muted">该账户暂无决策记录。</p>}
      {displayedDecisions && displayedDecisions.length > 0 && <div className="space-y-2">
        {[...displayedDecisions].reverse().slice(0, 100).map((decision, index) => <article key={`${decision.at_ms}-${decision.bar_time_ms}-${index}`} className="rounded-btn border border-border bg-base/30 p-3">
          <div className="flex flex-wrap items-start gap-2"><div className="mr-auto"><div className="text-xs font-semibold text-foreground">{decisionTarget(decision.target)} · {tradeLabel(decision.status)}</div><div className="mt-1 text-[11px] text-muted">记录 {fmtDate(decision.at_ms)} · 信号 K 线 {fmtDate(decision.bar_time_ms)}</div></div><span className="text-[11px] text-secondary">权益 {fmtNumber(decision.equity)} · 新增成交 {decision.trades_added}</span></div>
          <p className="mt-2 text-xs leading-relaxed text-secondary">{decision.reason}</p>
          <details className="mt-2 text-[11px] text-muted"><summary className="cursor-pointer">指标与条件</summary><div className="mt-2 grid gap-2 sm:grid-cols-2">
            <div className="rounded-btn border border-border p-2"><div className="font-medium text-secondary">指标</div>{Object.keys(decision.metrics).length ? Object.entries(decision.metrics).map(([key, value]) => <div key={key} className="mt-1 flex justify-between gap-2"><span>{key}</span><span className="font-mono">{value == null ? '缺失' : String(value)}</span></div>) : <div className="mt-1">无指标</div>}</div>
            <div className="rounded-btn border border-border p-2"><div className="font-medium text-secondary">条件</div>{Object.keys(decision.conditions).length ? Object.entries(decision.conditions).map(([key, value]) => <div key={key} className="mt-1 flex justify-between gap-2"><span>{key}</span><span>{value ? '是' : '否'}</span></div>) : <div className="mt-1">无条件记录</div>}</div>
          </div></details>
        </article>)}
      </div>}
    </div>}

    {tab === 'reviews' && <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 rounded-btn border border-border p-3">
        <BookOpen className="h-4 w-4 text-accent" /><div className="mr-auto min-w-[220px]"><div className="text-xs font-semibold">每日确定性复盘</div><div className="mt-1 text-[11px] text-muted">基于去重决策与实际账本统计；此步骤不调用 AI。</div></div>
        <button type="button" onClick={() => { void createReview() }} disabled={reviewPending} className="inline-flex items-center gap-2 rounded-btn border border-border px-3 py-2 text-xs text-secondary disabled:opacity-50">{reviewPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}{reviewPending ? '生成中…' : '生成今日固定复盘'}</button>
        <button type="button" onClick={() => { void interpretReview() }} disabled={aiPending || reviewPending} className="inline-flex items-center gap-2 rounded-btn border border-accent/40 bg-accent/10 px-3 py-2 text-xs text-accent disabled:opacity-50"><Sparkles className="h-3.5 w-3.5" />{aiPending ? 'AI 解读中…' : '生成并 AI 解读今日复盘'}</button>
      </div>
      <p className="text-[11px] text-muted">每天北京时间 00:10 后自动统计前一日，不调用 AI。AI 解读为可选操作，会向已配置模型发送匿名化的权益、成交/拦截统计与研究指标并消耗额度；解读与统计分开展示，不会修改策略或发单。</p>
      {(reviewNotice || aiNotice) && <p role="status" className="text-xs text-accent">{reviewNotice || aiNotice}</p>}
      {reviewError && <p role="alert" className="rounded-btn border border-danger/40 bg-danger/10 p-3 text-xs text-danger">{reviewError}</p>}
      {aiError && <p role="alert" className="rounded-btn border border-danger/40 bg-danger/10 p-3 text-xs text-danger">{aiError}</p>}
      {reviewsError && <p role="alert" className="rounded-btn border border-danger/40 bg-danger/10 p-3 text-xs text-danger">复盘列表读取失败：{reviewsError}。已有报告仍保留。</p>}
      {reviewsLoading && reviews === null && <p role="status" className="py-5 text-center text-xs text-muted">正在读取每日复盘…</p>}
      {!reviewsLoading && reviews !== null && reviews.length === 0 && <p className="rounded-btn border border-border p-5 text-center text-xs text-muted">暂无每日复盘。可以生成一份固定统计报告。</p>}
      {latestReview && <article className="space-y-3 rounded-btn border border-border bg-base/30 p-3">
        <div className="flex flex-wrap items-start gap-2"><div className="mr-auto"><h3 className="text-xs font-semibold">{latestReview.date} 复盘</h3><p className="mt-1 text-[11px] text-muted">生成时间 {fmtDate(latestReview.created_at_ms)} · {latestReview.method}</p></div><span className="rounded-full border border-border px-2 py-1 text-[10px] text-secondary">固定统计</span></div>
        <p className="whitespace-pre-wrap text-xs leading-relaxed text-secondary">{latestReview.summary}</p>
        <div className="overflow-x-auto rounded-btn border border-border"><table className="w-full min-w-[700px] text-left text-[11px]"><thead className="border-b border-border bg-elevated/40 text-muted"><tr><th className="px-2 py-2">策略账户</th><th>检查 / 动作 / 阻止</th><th>最近原因</th><th>成交</th><th>权益</th></tr></thead><tbody>
          {latestReview.accounts.length ? latestReview.accounts.map(account => <tr key={account.id} className="border-b border-border/50 last:border-0"><td className="px-2 py-2 text-foreground">{account.name}<div className="text-[10px] text-muted">{account.id}</div></td><td className="py-2 text-secondary">{account.checks} / {account.actions} / {account.blocked}</td><td className="max-w-[320px] py-2 text-secondary">{account.last_reason ?? '—'}</td><td className="py-2 text-secondary">{account.trade_count}</td><td className="py-2 font-mono text-secondary">{fmtNumber(account.equity)}</td></tr>)
            : <tr><td colSpan={5} className="px-2 py-4 text-center text-muted">报告没有策略账户统计。</td></tr>}
        </tbody></table></div>
        <div className="rounded-btn border border-border p-3"><div className="text-[11px] font-semibold text-secondary">研究候选 · {latestReview.candidates.length}</div>
          {latestReview.candidates.length > 0 && <ul className="mt-2 space-y-1 text-[11px] text-muted">{latestReview.candidates.slice(0, 20).map((candidate, index) => <li key={index}>{candidateText(candidate, index)}</li>)}</ul>}
        </div>
        {latestReview.ai?.content && <div className="rounded-btn border border-accent/30 bg-accent/5 p-3"><div className="flex flex-wrap items-center gap-2"><Sparkles className="h-3.5 w-3.5 text-accent" /><div className="text-[11px] font-semibold text-accent">AI 解读 · {latestReview.ai.model}</div><div className="ml-auto text-[10px] text-muted">{fmtDate(latestReview.ai.created_at_ms)}</div></div><p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-secondary">{latestReview.ai.content}</p></div>}
      </article>}
    </div>}
  </section>
}
