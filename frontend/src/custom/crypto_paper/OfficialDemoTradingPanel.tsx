import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, LoaderCircle, Pause, Play, RefreshCcw, RotateCcw, ShieldCheck } from 'lucide-react'
import { QK } from '@/lib/queryKeys'
import { cryptoApi, type CryptoStrategyAccount, type CryptoSymbol, type DemoExchange } from './client'
import {
  demoExecutionApi,
  type DemoTradingAction,
  type DemoTradingArmRequest,
  type DemoTradingOrder,
  type DemoTradingPlan,
  type DemoTradingStatus,
} from './demo-execution-client'

const SYMBOLS: CryptoSymbol[] = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
const ACTIONS: Array<{ value: DemoTradingAction; label: string }> = [
  { value: 'open_long', label: '开多' },
  { value: 'open_short', label: '开空' },
  { value: 'close_long', label: '平多' },
  { value: 'close_short', label: '平空' },
]
const FIELD_CLASS = 'mt-1 w-full min-w-0 rounded-btn border border-border bg-base px-3 py-2 text-sm text-foreground outline-none focus:border-accent focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60'
const BUTTON_CLASS = 'inline-flex min-h-9 items-center justify-center gap-2 rounded-btn border border-border px-3 py-2 text-xs font-medium text-secondary transition-colors hover:border-accent hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50'

type PlanState = 'ready' | 'submitting' | 'submitted' | 'unknown'

function isOpenAction(action: DemoTradingAction) {
  return action === 'open_long' || action === 'open_short'
}

function actionLabel(action: string) {
  return ACTIONS.find(item => item.value === action)?.label ?? action
}

function orderStateLabel(state: DemoTradingOrder['state']) {
  if (state === 'confirmed') return '已确认'
  if (state === 'rejected') return '已拒绝'
  if (state === 'uncertain') return '结果不明'
  return '已提交，待核对'
}

function formatTime(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value) || value <= 0) return '—'
  return new Date(value).toLocaleString('zh-CN')
}

function formatNumber(value: number | string | null | undefined, digits = 4) {
  if (value == null || value === '') return '—'
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : String(value)
}

function errorText(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function isSourceAccount(account: CryptoStrategyAccount, symbol: CryptoSymbol): boolean {
  return account.exchange === 'bitget' && account.market === 'usdm' && account.symbol === symbol && account.effective_readonly !== true
}

function OrderSummary({ order, onReconcile, reconcilePending }: {
  order: DemoTradingOrder
  onReconcile?: (order: DemoTradingOrder) => void
  reconcilePending?: boolean
}) {
  const pendingCheck = ['pending', 'submitted', 'uncertain'].includes(order.state)
  return <div className={`rounded-btn border p-3 ${order.state === 'uncertain' ? 'border-amber-500/40 bg-amber-500/5' : 'border-border bg-base'}`}>
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs font-semibold text-foreground">{actionLabel(order.action)} · {order.symbol}</span>
      <span className={`rounded-full px-2 py-0.5 text-[11px] ${order.state === 'confirmed' ? 'bg-accent/10 text-accent' : order.state === 'rejected' ? 'bg-danger/10 text-danger' : 'bg-amber-500/10 text-amber-300'}`}>{orderStateLabel(order.state)}</span>
      <span className="ml-auto text-[11px] text-muted">{formatTime(order.created_at_ms)}</span>
    </div>
    <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-muted sm:grid-cols-4">
      <span>估算金额 {formatNumber(order.estimated_notional, 2)} USDT</span>
      <span>数量 {formatNumber(order.quantity)}</span>
      <span>客户端编号 <code className="break-all">{order.client_id}</code></span>
      <span className="break-all">交易所编号 {order.order_id || '待返回'}{order.exchange_state ? ` · ${order.exchange_state}` : ''}</span>
    </div>
    {pendingCheck && <div className="mt-2 flex flex-wrap items-center gap-2">
      <p className="text-[11px] leading-relaxed text-amber-300">不要重复提交。客户端编号查询只做对账，不会再次发单。</p>
      {onReconcile && <button type="button" onClick={() => onReconcile(order)} disabled={reconcilePending} className={BUTTON_CLASS}>
        {reconcilePending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}查询状态（仅对账）
      </button>}
    </div>}
  </div>
}

export function OfficialDemoTradingPanel({ exchange, configured }: { exchange: DemoExchange; configured: boolean }) {
  const queryClient = useQueryClient()
  const contextRef = useRef({ exchange, configured, generation: 0 })
  const usedPlanIds = useRef(new Set<string>())
  const unidentifiedSubmission = useRef(false)
  const armGeneration = useRef(0)
  const [symbol, setSymbol] = useState<CryptoSymbol>('BTCUSDT')
  const [action, setAction] = useState<DemoTradingAction>('open_long')
  const [planNotional, setPlanNotional] = useState('10')
  const [maxNotional, setMaxNotional] = useState('10')
  const [dailyLossLimit, setDailyLossLimit] = useState('5')
  const [bindRules, setBindRules] = useState(false)
  const [sourceId, setSourceId] = useState('')
  const [sourceAccounts, setSourceAccounts] = useState<CryptoStrategyAccount[]>([])
  const [sourceLoading, setSourceLoading] = useState(false)
  const [sourceError, setSourceError] = useState<string | null>(null)
  const [confirmArm, setConfirmArm] = useState(false)
  const [confirmSubmit, setConfirmSubmit] = useState(false)
  const [armPending, setArmPending] = useState(false)
  const [pausePending, setPausePending] = useState(false)
  const [planPending, setPlanPending] = useState(false)
  const [submitPending, setSubmitPending] = useState(false)
  const [reconcilePending, setReconcilePending] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [plan, setPlan] = useState<DemoTradingPlan | null>(null)
  const [planState, setPlanState] = useState<PlanState>('ready')
  const [currentOrder, setCurrentOrder] = useState<DemoTradingOrder | null>(null)
  const [submissionBlocked, setSubmissionBlocked] = useState(false)
  const [now, setNow] = useState(Date.now())

  useLayoutEffect(() => {
    if (contextRef.current.exchange === exchange && contextRef.current.configured === configured) return
    const previous = contextRef.current
    contextRef.current = { exchange, configured, generation: contextRef.current.generation + 1 }
    if (previous.configured) {
      const previousKey = QK.demoTrading(previous.exchange)
      void queryClient.cancelQueries({ queryKey: previousKey, exact: true }).then(() => queryClient.removeQueries({ queryKey: previousKey, exact: true }))
    }
    usedPlanIds.current.clear()
    unidentifiedSubmission.current = false
    setSymbol('BTCUSDT')
    setAction('open_long')
    setPlanNotional('10')
    setMaxNotional('10')
    setDailyLossLimit('5')
    setBindRules(false)
    setSourceId('')
    setSourceAccounts([])
    setSourceLoading(false)
    setSourceError(null)
    setConfirmArm(false)
    setConfirmSubmit(false)
    setArmPending(false)
    setPausePending(false)
    setPlanPending(false)
    setSubmitPending(false)
    setReconcilePending(false)
    setMessage(null)
    setError(null)
    setPlan(null)
    setPlanState('ready')
    setCurrentOrder(null)
    setSubmissionBlocked(false)
  }, [configured, exchange, queryClient])

  const statusQuery = useQuery({
    queryKey: QK.demoTrading(exchange),
    queryFn: ({ signal }) => demoExecutionApi.status(exchange, signal),
    enabled: configured,
    retry: false,
    staleTime: 5_000,
    refetchInterval: query => query.state.data?.armed ? 20_000 : false,
  })
  const status = configured ? statusQuery.data : undefined
  const candidateAccounts = useMemo(() => sourceAccounts.filter(account => isSourceAccount(account, symbol)), [sourceAccounts, symbol])
  const selectedSource = candidateAccounts.find(account => account.id === sourceId)
  const activeSymbol = status?.armed && SYMBOLS.includes(status.symbol as CryptoSymbol) ? status.symbol as CryptoSymbol : symbol
  const maxNotionalValue = Number(maxNotional)
  const dailyLossValue = Number(dailyLossLimit)
  const planNotionalValue = Number(planNotional)
  const activeMaxNotional = status?.armed ? status.max_notional : maxNotionalValue
  const validArmLimits = Number.isFinite(maxNotionalValue) && maxNotionalValue >= 5 && maxNotionalValue <= 100
    && Number.isFinite(dailyLossValue) && dailyLossValue >= 0.1 && dailyLossValue <= 100
  const sourceIsValid = !bindRules || Boolean(selectedSource)
  const hasUnresolvedOrders = Boolean(status?.orders.some(order => ['pending', 'uncertain', 'submitted'].includes(order.state)))
  const canEnable = configured && statusQuery.isSuccess && status?.armed === false && confirmArm
    && validArmLimits && sourceIsValid && !armPending && !submissionBlocked && !hasUnresolvedOrders
  const planExpired = Boolean(plan && now >= plan.expires_at_ms)
  const canPreview = configured && status?.armed === true && !statusQuery.isError && !armPending && !pausePending
    && !planPending && !submitPending && !reconcilePending && !submissionBlocked && !hasUnresolvedOrders
    && Number.isFinite(planNotionalValue) && planNotionalValue >= 5 && planNotionalValue <= 100
    && (!isOpenAction(action) || planNotionalValue <= Math.min(activeMaxNotional || 10, 100))
  const canSubmit = configured && status?.armed === true && !statusQuery.isError

  useEffect(() => {
    if (configured) return
    const key = QK.demoTrading(exchange)
    void queryClient.cancelQueries({ queryKey: key, exact: true }).then(() => queryClient.removeQueries({ queryKey: key, exact: true }))
  }, [configured, exchange, queryClient])

  useEffect(() => {
    if (!bindRules || !configured) {
      setSourceAccounts([])
      setSourceLoading(false)
      setSourceError(null)
      return
    }
    let alive = true
    setSourceLoading(true)
    setSourceError(null)
    void cryptoApi.strategyAccounts().then(result => {
      if (alive) {
        setSourceAccounts(result.accounts)
        setSourceId(current => result.accounts.some(account => account.id === current) ? current : '')
      }
    }).catch(reason => {
      if (alive) setSourceError(errorText(reason, '无法读取本地策略账户'))
    }).finally(() => {
      if (alive) setSourceLoading(false)
    })
    return () => { alive = false }
  }, [bindRules, configured])

  useEffect(() => {
    if (!status) return
    if (SYMBOLS.includes(status.symbol as CryptoSymbol)) setSymbol(status.symbol as CryptoSymbol)
    if (Number.isFinite(status.max_notional)) setMaxNotional(String(status.max_notional))
    if (Number.isFinite(status.daily_loss_limit)) setDailyLossLimit(String(status.daily_loss_limit))
  }, [status?.symbol, status?.max_notional, status?.daily_loss_limit])

  useEffect(() => {
    setSubmissionBlocked(unidentifiedSubmission.current || Boolean(status?.orders.some(order => ['pending', 'uncertain', 'submitted'].includes(order.state))))
  }, [status?.orders])

  useEffect(() => {
    if (!plan) return
    const timer = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(timer)
  }, [plan])

  const isCurrent = (generation: number, targetExchange: DemoExchange) =>
    contextRef.current.generation === generation && contextRef.current.exchange === targetExchange && contextRef.current.configured

  const refreshStatusAndSnapshots = () => {
    void queryClient.invalidateQueries({ queryKey: QK.demoTrading(exchange) })
    void queryClient.invalidateQueries({ queryKey: QK.demoSnapshots(exchange) })
  }

  const handleArm = async (enabled: boolean) => {
    if (!configured || (enabled ? armPending : pausePending)) return
    if (enabled && !canEnable) return
    const generation = contextRef.current.generation
    const sequence = ++armGeneration.current
    if (enabled) setArmPending(true)
    else setPausePending(true)
    setError(null)
    setMessage(null)
    const body: DemoTradingArmRequest = {
      enabled,
      confirm_demo: true,
      symbol: enabled ? symbol : status?.symbol as CryptoSymbol ?? symbol,
      source_account_id: enabled && bindRules ? sourceId || null : enabled ? null : status?.source_account_id ?? null,
      max_notional: enabled ? maxNotionalValue : status?.max_notional ?? (validArmLimits ? maxNotionalValue : 10),
      daily_loss_limit: enabled ? dailyLossValue : status?.daily_loss_limit ?? (Number.isFinite(dailyLossValue) && dailyLossValue >= 0.1 && dailyLossValue <= 100 ? dailyLossValue : 5),
    }
    try {
      const nextStatus = await demoExecutionApi.arm(exchange, body)
      if (!isCurrent(generation, exchange) || sequence !== armGeneration.current) return
      queryClient.setQueryData<DemoTradingStatus>(QK.demoTrading(exchange), nextStatus)
      setMessage(enabled ? '官方模拟执行已启用。重启后必须重新启用。' : '官方模拟执行已暂停；不会自动平掉已有仓位。')
      setConfirmArm(false)
      if (!enabled && !submitPending && !planPending && planState === 'ready') {
        setPlan(null)
        setConfirmSubmit(false)
      }
    } catch (reason) {
      if (isCurrent(generation, exchange) && sequence === armGeneration.current) setError(errorText(reason, enabled ? '启用模拟执行失败' : '暂停模拟执行失败'))
    } finally {
      if (isCurrent(generation, exchange)) {
        if (enabled) setArmPending(false)
        else setPausePending(false)
      }
    }
  }

  const handlePlan = async () => {
    if (!canPreview) return
    if (isOpenAction(action) && planNotionalValue > Math.min(activeMaxNotional, 100)) {
      setError(`新增开仓估算金额不能超过 ${Math.min(activeMaxNotional, 100)} USDT。`)
      return
    }
    const generation = contextRef.current.generation
    setPlanPending(true)
    setError(null)
    setMessage(null)
    setPlan(null)
    setConfirmSubmit(false)
    try {
      const result = await demoExecutionApi.plan(exchange, { symbol: activeSymbol, action, notional: planNotionalValue })
      if (!isCurrent(generation, exchange)) return
      const estimatedNotional = Number(result.estimated_notional)
      if (isOpenAction(action) && (!Number.isFinite(estimatedNotional) || estimatedNotional > Math.min(activeMaxNotional, 100))) {
        setError('服务器返回的开仓计划超过单笔上限，已拒绝显示为可提交计划。')
        return
      }
      setPlan(result)
      setPlanState('ready')
      setNow(Date.now())
    } catch (reason) {
      if (isCurrent(generation, exchange)) setError(errorText(reason, '生成模拟交易计划失败'))
    } finally {
      if (isCurrent(generation, exchange)) setPlanPending(false)
    }
  }

  const handleSubmit = async () => {
    if (!plan || planState !== 'ready' || !confirmSubmit || submitPending || planExpired || submissionBlocked || !canSubmit) return
    if (usedPlanIds.current.has(plan.id)) {
      setPlanState('unknown')
      setSubmissionBlocked(true)
      setError('该计划已经提交过或提交状态不明，不能再次提交。请先查询模拟订单状态。')
      return
    }
    usedPlanIds.current.add(plan.id)
    const consumedPlanId = plan.id
    const generation = contextRef.current.generation
    setPlanState('submitting')
    setSubmitPending(true)
    setConfirmSubmit(false)
    setError(null)
    setMessage(null)
    try {
      const result = await demoExecutionApi.submit(exchange, consumedPlanId)
      if (!isCurrent(generation, exchange)) return
      setCurrentOrder(result.order)
      setPlanState('submitted')
      const unresolved = result.order.state === 'uncertain' || result.order.state === 'submitted'
      setSubmissionBlocked(unresolved)
      queryClient.setQueryData<DemoTradingStatus>(QK.demoTrading(exchange), current => current ? {
        ...current,
        armed: result.order.state === 'uncertain' ? false : current.armed,
        orders: [result.order, ...current.orders.filter(order => order.client_id !== result.order.client_id)],
      } : current)
      setMessage(result.order.state === 'uncertain'
        ? '结果不明，已暂停，仅查单；不要重新提交。'
        : unresolved ? '提交已受理但尚未确认；仅查询状态，不要重新提交。' : `模拟订单${orderStateLabel(result.order.state)}。`)
      refreshStatusAndSnapshots()
    } catch (reason) {
      if (isCurrent(generation, exchange)) {
        setPlanState('unknown')
        setSubmissionBlocked(true)
        unidentifiedSubmission.current = true
        setError(`${errorText(reason, '提交结果暂不可确认')}。该计划已锁定，请不要重发；先刷新状态并核对模拟账户订单。`)
        refreshStatusAndSnapshots()
      }
    } finally {
      if (isCurrent(generation, exchange)) setSubmitPending(false)
    }
  }

  const handleReconcile = async (orderToReconcile: DemoTradingOrder) => {
    if (!configured || !orderToReconcile.client_id || !['pending', 'uncertain', 'submitted'].includes(orderToReconcile.state) || reconcilePending) return
    const generation = contextRef.current.generation
    setReconcilePending(true)
    setError(null)
    try {
      const result = await demoExecutionApi.reconcile(exchange, orderToReconcile.client_id)
      if (!isCurrent(generation, exchange)) return
      setCurrentOrder(current => current?.client_id === result.order.client_id ? result.order : current)
      const latest = queryClient.getQueryData<DemoTradingStatus>(QK.demoTrading(exchange))
      const orders = latest
        ? [result.order, ...latest.orders.filter(order => order.client_id !== result.order.client_id)]
        : [result.order]
      if (latest) queryClient.setQueryData<DemoTradingStatus>(QK.demoTrading(exchange), { ...latest, orders })
      const unresolved = orders.some(order => ['pending', 'uncertain', 'submitted'].includes(order.state))
      if (!unresolved && result.order.state === 'confirmed') unidentifiedSubmission.current = false
      setSubmissionBlocked(unidentifiedSubmission.current || unresolved)
      setMessage(unresolved ? '订单仍未确认；可以继续查询，但不能重新提交。' : `订单状态已更新：${orderStateLabel(result.order.state)}。`)
      refreshStatusAndSnapshots()
    } catch (reason) {
      if (isCurrent(generation, exchange)) setError(errorText(reason, '查询模拟订单状态失败；不要重复提交。'))
    } finally {
      if (isCurrent(generation, exchange)) setReconcilePending(false)
    }
  }

  const recentOrders = [...(status?.orders ?? [])].sort((a, b) => b.created_at_ms - a.created_at_ms)
  const recentDecisions = [...(status?.decisions ?? [])].reverse()

  return <section className="rounded-card border border-border bg-surface p-3 md:p-4">
    <div className="flex flex-wrap items-start gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-foreground">官方模拟交易控制</h2>
          <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/35 bg-amber-500/10 px-2.5 py-1 text-[11px] font-medium text-amber-300"><ShieldCheck className="h-3.5 w-3.5" />模拟专用 · 默认关闭</span>
        </div>
        <p className="mt-1 text-xs leading-relaxed text-muted">此处仅向 {exchange === 'okx' ? 'OKX' : 'Bitget'} 官方模拟环境发出受限计划。账户余额、持仓、委托和成交表格仍为只读。</p>
      </div>
      {configured && <button type="button" onClick={() => { void handleArm(false) }} disabled={pausePending} className={`${BUTTON_CLASS} shrink-0`}>
        {pausePending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Pause className="h-3.5 w-3.5" />}
        暂停模拟执行
      </button>}
    </div>

    <div className="mt-3 rounded-btn border border-amber-500/30 bg-amber-500/5 p-3 text-[11px] leading-relaxed text-amber-200">
      <p className="font-semibold">操作步骤：启用模拟执行 → 预览计划 → 明确确认模拟下单</p>
      <p className="mt-1">必须使用官方模拟专用 API Key，并在交易所授权模拟交易权限。程序只按单向、逐仓、1 倍计划交易，不会修改交易所的持仓模式或杠杆；请先在官网确认模式与杠杆设置。</p>
      <p className="mt-1">新增开仓单笔估算默认 10 USDT、上限最多 100 USDT；日亏阈值只阻止新增开仓，不会自动平掉已有仓位。重启后执行会关闭，需重新启用。</p>
      <p className="mt-1">若提交结果不明，不要重复提交；用客户端订单编号查询对账。公开图表价格与模拟账户撮合价可能不同。</p>
    </div>

    {!configured
      ? <div className="mt-3 rounded-btn border border-dashed border-border px-4 py-6 text-center text-sm text-muted">先连接当前交易所的官方模拟账户后才能配置执行。本状态下不会请求私有交易状态、计划或订单。</div>
      : <>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {statusQuery.isPending && <span className="text-xs text-muted">正在读取模拟执行状态…</span>}
          {statusQuery.isError && <span role="alert" className="text-xs text-amber-300">模拟执行状态读取失败。可以随时暂停；请刷新并确认已启用后再预览或提交。</span>}
          {status && <>
            <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${status.armed ? 'bg-accent/10 text-accent' : 'bg-elevated text-muted'}`}>{status.armed ? '已启用' : '已暂停'}</span>
            <span className="text-xs text-secondary">{status.auto ? '自动规则评估' : '手动计划'} · {status.symbol} · 新增开仓估算上限 {formatNumber(status.max_notional, 2)} USDT · 日亏阈值 {formatNumber(status.daily_loss_limit, 2)} USDT</span>
            {status.managed && <span className="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted">本站托管</span>}
          </>}
          <button type="button" onClick={() => { void statusQuery.refetch() }} disabled={statusQuery.isFetching} className={`${BUTTON_CLASS} ml-auto min-h-8 px-2`}>
            <RefreshCcw className={`h-3.5 w-3.5 ${statusQuery.isFetching ? 'animate-spin' : ''}`} />刷新状态
          </button>
        </div>
        {status?.reason && <p className="mt-2 text-[11px] leading-relaxed text-muted">状态说明：{status.reason}</p>}

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="text-xs text-secondary">交易对
            <select aria-label="模拟执行交易对" value={activeSymbol} onChange={event => { setSymbol(event.target.value as CryptoSymbol); setSourceId(''); setPlan(null); setConfirmSubmit(false) }} className={FIELD_CLASS} disabled={armPending || status?.armed === true || planPending || submitPending}>
              {SYMBOLS.map(item => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label className="text-xs text-secondary">新增开仓估算上限（USDT）
            <input aria-label="单笔开仓估算上限" type="number" min="5" max="100" step="1" value={maxNotional} onChange={event => setMaxNotional(event.target.value)} className={FIELD_CLASS} disabled={armPending || status?.armed === true} />
            <span className="mt-1 block text-[11px] text-muted">5–100 USDT；默认 10 USDT。仅限制新增开仓估算金额。</span>
          </label>
          <label className="text-xs text-secondary">日亏停止新增开仓阈值（USDT）
            <input aria-label="日亏停止阈值" type="number" min="0.1" max="100" step="0.1" value={dailyLossLimit} onChange={event => setDailyLossLimit(event.target.value)} className={FIELD_CLASS} disabled={armPending || status?.armed === true} />
            <span className="mt-1 block text-[11px] text-muted">0.1–100 USDT；触发后只拦新增开仓，不自动平仓。</span>
          </label>
          <div className="rounded-btn border border-border bg-base p-3 text-[11px] leading-relaxed text-muted">
            单向逐仓 1 倍由计划约束；程序不会更改交易所账户设置。若选择自动规则，反向信号先平仓，下一根 K 线再评估是否开仓。
          </div>
        </div>

        <label className="mt-3 flex items-start gap-2 rounded-btn border border-border bg-base p-3 text-xs text-secondary">
          <input type="checkbox" aria-label="自动绑定本地策略规则" checked={bindRules} disabled={status?.armed === true || armPending} onChange={event => { setBindRules(event.target.checked); setSourceId('') }} className="mt-0.5 accent-[var(--color-accent)]" />
          <span><span className="font-medium text-foreground">启用自动规则绑定</span><span className="mt-1 block text-[11px] leading-relaxed text-muted">勾选即确认：只复制所选本地策略账户的规则，反向信号先平仓、下根 K 线再评估；不会修改、启用或连接原本地账户。</span></span>
        </label>
        {bindRules && <div className="mt-3 space-y-2 rounded-btn border border-border bg-base p-3">
          <label className="block text-xs text-secondary">规则来源账户
            <select aria-label="自动规则来源账户" value={sourceId} onChange={event => setSourceId(event.target.value)} className={FIELD_CLASS} disabled={sourceLoading || status?.armed === true || armPending}>
              <option value="">请选择同交易对的 Bitget USDT 本位策略账户</option>
              {candidateAccounts.map(account => <option key={account.id} value={account.id}>{account.name} · {account.symbol} · {account.strategy_id}{account.enabled ? ' · 原账户运行中' : ' · 原账户已暂停'}</option>)}
            </select>
          </label>
          {sourceLoading && <p className="text-[11px] text-muted">正在读取本地策略账户；这里只复制规则配置。</p>}
          {sourceError && <p role="alert" className="text-[11px] text-danger">读取本地策略账户失败：{sourceError}</p>}
          {!sourceLoading && !sourceError && candidateAccounts.length === 0 && <p className="text-[11px] text-muted">没有同交易对且可用的 Bitget USDT 本位策略账户；可取消勾选改用手动计划。</p>}
          {selectedSource && <p className="text-[11px] text-muted">已选规则：{selectedSource.name}。启用自动评估不会改动该账户的状态、仓位或参数。</p>}
        </div>}

        {status?.armed !== true && <div className="mt-3 space-y-2">
          <label className="flex items-start gap-2 rounded-btn border border-amber-500/25 bg-amber-500/5 p-3 text-xs text-secondary">
            <input type="checkbox" aria-label="确认官方模拟执行" checked={confirmArm} disabled={armPending || statusQuery.isError} onChange={event => setConfirmArm(event.target.checked)} className="mt-0.5 accent-[var(--color-accent)]" />
            <span>我确认已在 {exchange === 'okx' ? 'OKX' : 'Bitget'} 官网切换到模拟模式、使用模拟专用交易权限 Key，并核对单向逐仓 1 倍设置。</span>
          </label>
          <button type="button" onClick={() => { void handleArm(true) }} disabled={!canEnable} className="inline-flex min-h-9 items-center justify-center gap-2 rounded-btn bg-accent px-4 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">
            {armPending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}启用模拟执行
          </button>
          {bindRules && !sourceIsValid && <p className="text-[11px] text-amber-300">请选择与执行交易对一致的规则来源账户后再启用。</p>}
          {!validArmLimits && <p role="alert" className="text-[11px] text-danger">上限必须为 5–100 USDT，日亏阈值必须为 0.1–100 USDT。</p>}
        </div>}

        <div className="mt-4 rounded-btn border border-border bg-base p-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto] sm:items-end">
            <label className="text-xs text-secondary">动作
              <select aria-label="模拟计划动作" value={action} onChange={event => { setAction(event.target.value as DemoTradingAction); setPlan(null); setConfirmSubmit(false) }} className={FIELD_CLASS} disabled={planPending || submitPending || submissionBlocked}>
                {ACTIONS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            <label className="text-xs text-secondary">{isOpenAction(action) ? '新增开仓估算金额（USDT）' : '平仓参考金额（USDT，实际按持仓全量减仓）'}
              <input aria-label="计划估算金额" type="number" min="5" max="100" step="0.01" value={planNotional} onChange={event => { setPlanNotional(event.target.value); setPlan(null); setConfirmSubmit(false) }} className={FIELD_CLASS} disabled={planPending || submitPending || submissionBlocked || !isOpenAction(action)} />
            </label>
            <button type="button" onClick={() => { void handlePlan() }} disabled={!canPreview} className={`${BUTTON_CLASS} min-h-10`}>
              {planPending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}预览计划
            </button>
          <span className="text-[11px] text-muted">新增开仓最多 {formatNumber(Math.min(activeMaxNotional || 10, 100), 2)} USDT；只减仓可按现有仓位全量平仓，仍受交易所最大单量约束。</span>
          </div>
          {submissionBlocked && <p role="alert" className="mt-3 rounded-btn border border-amber-500/30 bg-amber-500/5 p-3 text-xs leading-relaxed text-amber-300">上一笔提交还未确认。暂停可用，但不会创建新计划或重发订单；先完成查单对账。</p>}
          {plan && <div className="mt-3 space-y-3 rounded-btn border border-accent/30 bg-accent/5 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="mr-auto text-xs font-semibold text-foreground">计划预览 · {plan.symbol} · {actionLabel(plan.action)}</h3>
              <span className={`text-[11px] ${planExpired ? 'text-danger' : 'text-muted'}`}>{planExpired ? '已过期，不能提交' : `有效至 ${formatTime(plan.expires_at_ms)}`}</span>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
              <div className="rounded-btn border border-border bg-base p-2"><div className="text-[11px] text-muted">数量</div><div className="mt-1 break-all font-mono text-foreground">{formatNumber(plan.quantity)} {plan.quantity_unit}</div></div>
              <div className="rounded-btn border border-border bg-base p-2"><div className="text-[11px] text-muted">估算金额</div><div className="mt-1 break-all font-mono text-foreground">{formatNumber(plan.estimated_notional, 2)} USDT</div></div>
              <div className="rounded-btn border border-border bg-base p-2"><div className="text-[11px] text-muted">参考价格</div><div className="mt-1 break-all font-mono text-foreground">{formatNumber(plan.price)}</div></div>
              <div className="rounded-btn border border-border bg-base p-2"><div className="text-[11px] text-muted">计划约束</div><div className="mt-1 text-foreground">{plan.leverage}x · {plan.margin_mode} · {plan.reduce_only ? '只减仓' : '新开仓'}</div></div>
            </div>
            {plan.warnings.length > 0 && <ul className="list-disc space-y-1 pl-5 text-[11px] leading-relaxed text-amber-300">{plan.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul>}
            {planState === 'unknown' && <p role="alert" className="text-xs leading-relaxed text-danger">服务器未能确认提交结果。此计划已锁定，禁止重发；只刷新状态并对账，不会再次发单。</p>}
            {planState === 'submitted' && currentOrder && <OrderSummary order={currentOrder} onReconcile={handleReconcile} reconcilePending={reconcilePending} />}
            {planState === 'ready' && <>
              <label className="flex items-start gap-2 rounded-btn border border-border bg-base p-3 text-xs text-secondary">
                <input type="checkbox" aria-label="确认提交模拟订单" checked={confirmSubmit} disabled={planExpired || planPending || submitPending || usedPlanIds.current.has(plan.id) || !canSubmit} onChange={event => setConfirmSubmit(event.target.checked)} className="mt-0.5 accent-[var(--color-accent)]" />
                <span>我已核对上述数量、估算金额、方向和有效期，明确确认向官方模拟账户提交此单。</span>
              </label>
              {!canSubmit && <p className="text-[11px] text-amber-300">明确提交前须先确认状态已启用。状态读取失败时请刷新状态。</p>}
              <button type="button" onClick={() => { void handleSubmit() }} disabled={!confirmSubmit || planExpired || planPending || submitPending || usedPlanIds.current.has(plan.id) || !canSubmit} className="inline-flex min-h-9 items-center justify-center gap-2 rounded-btn bg-danger px-4 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">
                {submitPending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}明确确认模拟下单
              </button>
            </>}
            {planState === 'submitting' && <p className="text-xs text-muted">提交中。不要关闭页面或重复提交…</p>}
          </div>}
        </div>
      </>}

    {message && <p role="status" className="mt-3 rounded-btn border border-accent/25 bg-accent/5 p-3 text-xs text-accent">{message}</p>}
    {error && <p role="alert" className="mt-3 rounded-btn border border-danger/25 bg-danger/5 p-3 text-xs text-danger">{error}</p>}

    {configured && status && <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div className="min-w-0">
        <h3 className="mb-2 text-xs font-semibold text-secondary">最近决策</h3>
        {recentDecisions.length === 0
          ? <div className="rounded-btn border border-dashed border-border px-3 py-5 text-center text-xs text-muted">暂无模拟决策记录。</div>
          : <div className="space-y-2">{recentDecisions.slice(0, 5).map((decision, index) => <div key={`${decision.at_ms}-${index}`} className="rounded-btn border border-border bg-base p-2 text-[11px]">
            <div className="flex flex-wrap items-center gap-2"><span className="font-medium text-foreground">{decision.status}</span>{decision.target && <span className="text-muted">目标 {decision.target}</span>}<span className="ml-auto text-muted">{formatTime(decision.at_ms)}</span></div>
            <p className="mt-1 leading-relaxed text-muted">{decision.reason || '—'}</p>
          </div>)}</div>}
      </div>
      <div className="min-w-0">
        <h3 className="mb-2 text-xs font-semibold text-secondary">最近模拟订单</h3>
        {recentOrders.length === 0
          ? <div className="rounded-btn border border-dashed border-border px-3 py-5 text-center text-xs text-muted">暂无模拟订单记录。</div>
          : <div className="space-y-2">{recentOrders.slice(0, 5).map(order => <OrderSummary key={order.client_id} order={order} onReconcile={handleReconcile} reconcilePending={reconcilePending} />)}</div>}
      </div>
    </div>}
    {configured && !status && statusQuery.isError && <div className="mt-4 rounded-btn border border-dashed border-border p-3 text-[11px] leading-relaxed text-muted"><AlertTriangle className="mr-1 inline h-3.5 w-3.5 text-amber-300" />最近决策与订单暂不可见；状态查询失败不影响上方暂停按钮。</div>}
  </section>
}
