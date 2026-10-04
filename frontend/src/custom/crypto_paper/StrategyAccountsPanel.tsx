import { Fragment, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Activity, ChevronDown, ChevronRight, Loader2, Pause, Play, RefreshCcw } from 'lucide-react'
import { AiStrategyDraftPanel } from './AiStrategyDraftPanel'
import {
  cryptoApi,
  type CryptoExchange,
  type CryptoMarket,
  type CryptoStrategyAccount,
  type CryptoStrategyAccountCreate,
  type CryptoStrategyAccountDetail,
  type CryptoStrategyDefinition,
  type CryptoStrategyDraft,
  type CryptoStrategyId,
  type CryptoStrategyInterval,
  type CryptoStrategyModel,
  type CryptoStrategyTrade,
  type CryptoSymbol,
} from './client'

const SYMBOLS: CryptoSymbol[] = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
const LEVERAGES = Array.from({ length: 20 }, (_, index) => index + 1)
const COMPARE_LEVERAGES = [1, 5, 10, 20]
const DEFAULT_MODEL: CryptoStrategyModel = {
  maintenance_margin_rate: '0.005',
  liquidation_fee_rate: '0.005',
  slippage_bps: 5,
  poll_seconds: 30,
}
const FALLBACK_STRATEGIES: CryptoStrategyDefinition[] = [
  { id: 'ema_trend', name: 'EMA 趋势', description: '均线趋势研究预设' },
  { id: 'channel_breakout', name: '通道突破', description: '通道突破研究预设' },
]

type FormState = {
  name: string
  exchange: CryptoExchange
  market: CryptoMarket
  symbol: CryptoSymbol
  strategy_id: CryptoStrategyId
  fast_period: string
  slow_period: string
  lookback: string
  interval: CryptoStrategyInterval
  leverage: string
  initial_cash: string
  allocation_pct: string
  stop_loss_pct: string
  take_profit_pct: string
}

const INITIAL_FORM: FormState = {
  name: '策略模拟账户',
  exchange: 'bitget',
  market: 'usdm',
  symbol: 'BTCUSDT',
  strategy_id: 'ema_trend',
  fast_period: '20',
  slow_period: '60',
  lookback: '20',
  interval: '1h',
  leverage: '1',
  initial_cash: '10000',
  allocation_pct: '10',
  stop_loss_pct: '2',
  take_profit_pct: '4',
}

function formatMoney(value: string | number | null | undefined, digits = 2) {
  if (value == null || value === '') return '—'
  const number = Number(value)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '—'
}

function formatPercent(value: number | null | undefined) {
  return value == null || !Number.isFinite(Number(value)) ? '—' : `${Number(value).toFixed(2)}%`
}

function formatRate(value: string | number | undefined) {
  const rate = Number(value)
  return Number.isFinite(rate) ? `${(rate * 100).toFixed(2)}%` : '—'
}

function formatTime(value: number | string | null | undefined) {
  if (value == null || value === '') return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

function requestError(cause: unknown, fallback: string) {
  return cause instanceof Error ? cause.message : fallback
}

function accountName(account: CryptoStrategyAccount, strategies: CryptoStrategyDefinition[]) {
  return strategies.find(item => item.id === account.strategy_id)?.name ?? account.strategy_id
}

function strategyParametersLabel(account: CryptoStrategyAccount) {
  const params = account.strategy_params ?? {}
  const read = (key: string, fallback: number) => {
    const value = Number(params[key])
    return Number.isInteger(value) && value > 0 ? value : fallback
  }
  return account.strategy_id === 'ema_trend'
    ? `EMA ${read('fast_period', 20)} / ${read('slow_period', 60)}`
    : `通道回看 ${read('lookback', 20)} 根 K 线`
}

function exchangeName(exchange: CryptoExchange) {
  return exchange === 'bitget' ? 'Bitget' : 'Binance'
}

function isCurrentAccount(account: CryptoStrategyAccount) {
  const serverReadOnly = 'effective_readonly' in account && account.effective_readonly === true
  return !serverReadOnly && account.exchange === 'bitget' && account.market === 'usdm'
}

function readOnlyAccountMessage(account: CryptoStrategyAccount) {
  if ('readonly_reason' in account && typeof account.readonly_reason === 'string' && account.readonly_reason.trim()) {
    return account.readonly_reason.trim()
  }
  return account.exchange === 'binance'
    ? 'Binance 账户仅作为历史记录保留，只能查看详情；启停和运行操作已禁用，不会请求行情或运行账户。'
    : '当前仅支持 Bitget USDT 本位合约；此账户仅保留只读查看，不会请求行情或运行账户。'
}

function takerFeeLabel(account: CryptoStrategyAccount) {
  return account.taker_fee_rate == null
    ? '待获取公开手续费率'
    : `成交手续费率 ${formatRate(account.taker_fee_rate)}`
}

function accountStatus(status: string) {
  const labels: Record<string, string> = {
    paused: '暂停', disabled: '已停用', enabled: '待检查', waiting: '等待首次估值', catching_up: '补账中', 'catching-up': '补账中', running: '运行中', active: '运行中', error: '估值待核对', readonly: '历史只读',
  }
  return labels[status.toLowerCase()] ?? (status || '状态未知')
}

function valuationMessage(account: CryptoStrategyAccount) {
  const status = account.status.toLowerCase()
  if (status === 'waiting') return '等待首次估值'
  if (status === 'catching_up' || status === 'catching-up') return '补账中'
  if (status === 'error') return '估值待核对'
  return account.equity == null ? '等待估值' : null
}

function valuationPending(account: CryptoStrategyAccount) {
  return valuationMessage(account) !== null
}

function mergeAccounts(current: CryptoStrategyAccount[] | null, incoming: CryptoStrategyAccount[]) {
  const byId = new Map((current ?? []).map(item => [item.id, item]))
  incoming.forEach(item => byId.set(item.id, item))
  return [...byId.values()]
}

function NavChart({ points }: { points: CryptoStrategyAccountDetail['nav'] }) {
  const stride = Math.max(1, Math.ceil(points.length / 300))
  const plottedPoints = points.filter((_, index) => index % stride === 0 || index === points.length - 1)
  const values = plottedPoints.map(point => Number(point.equity)).filter(Number.isFinite)
  if (!values.length) return <div className="flex h-28 items-center justify-center rounded-btn border border-border text-xs text-muted">暂无净值记录</div>

  const width = 600
  const height = 150
  const padding = 12
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || Math.max(Math.abs(max) * 0.01, 1)
  const coordinates = plottedPoints.flatMap((point, index) => {
    const value = Number(point.equity)
    if (!Number.isFinite(value)) return []
    const x = plottedPoints.length === 1 ? width / 2 : padding + (index / (plottedPoints.length - 1)) * (width - padding * 2)
    const y = height - padding - ((value - min) / span) * (height - padding * 2)
    return [`${x},${y}`]
  })
  const path = coordinates.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point}`).join(' ')
  const singlePoint = coordinates.length === 1 ? coordinates[0].split(',').map(Number) : null

  return <div className="rounded-btn border border-border p-2">
    <div className="mb-1 flex items-center justify-between text-[11px] text-muted"><span>账户净值</span><span>{points.length} 个记录点</span></div>
    <svg viewBox={`0 0 ${width} ${height}`} className="h-32 w-full text-accent" role="img" aria-label="策略账户净值曲线" preserveAspectRatio="none">
      <line x1={padding} x2={width - padding} y1={height - padding} y2={height - padding} stroke="currentColor" strokeOpacity="0.2" />
      <path d={path} fill="none" stroke="currentColor" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />
      {singlePoint && <circle cx={singlePoint[0]} cy={singlePoint[1]} r="4" fill="currentColor" />}
    </svg>
  </div>
}

function eventLabel(event: CryptoStrategyTrade) {
  if (event.kind === 'funding') return '资金费'
  if (event.kind === 'liquidation') return '强平'
  return event.action ?? '策略成交'
}

function eventTime(event: CryptoStrategyTrade) {
  return formatTime(event.at_ms ?? event.at)
}

function positionRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function positionNumber(position: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = position[key]
    if (typeof value !== 'number' && typeof value !== 'string') continue
    if (typeof value === 'string' && value.trim() === '') continue
    const number = Number(value)
    if (Number.isFinite(number)) return number
  }
  return null
}

function positionText(position: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = position[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

function positionSummary(value: unknown, market: CryptoMarket) {
  const position = positionRecord(value)
  if (!position) return []

  const fields: string[] = []
  if (market === 'usdm') {
    const side = positionText(position, 'side', 'direction')?.toLowerCase()
    if (side) fields.push(`方向 ${({ long: '多', short: '空', buy: '买入', sell: '卖出' } as Record<string, string>)[side] ?? side}`)
  }

  const quantity = positionNumber(position, 'qty', 'quantity')
  if (quantity !== null) fields.push(`数量 ${formatMoney(quantity, 6)}`)

  if (market === 'spot') {
    const cost = positionNumber(position, 'cost')
    if (cost !== null) fields.push(`现货成本 ${formatMoney(cost, 4)} USDT`)
  } else {
    const entry = positionNumber(position, 'entry', 'entry_price')
    if (entry !== null) fields.push(`入场价 ${formatMoney(entry, 4)} USDT`)
    const margin = positionNumber(position, 'margin')
    if (margin !== null) fields.push(`保证金 ${formatMoney(margin, 4)} USDT`)
    const leverage = positionNumber(position, 'leverage')
    if (leverage !== null) fields.push(`杠杆 ${formatMoney(leverage, 2)}x`)
  }

  return fields
}

export function StrategyAccountsPanel({ onInspectAccount }: {
  onInspectAccount?: (account: CryptoStrategyAccount) => void
} = {}) {
  const [strategies, setStrategies] = useState<CryptoStrategyDefinition[]>(FALLBACK_STRATEGIES)
  const [strategyError, setStrategyError] = useState('')
  const [accounts, setAccounts] = useState<CryptoStrategyAccount[] | null>(null)
  const [runtimeRunning, setRuntimeRunning] = useState(false)
  const [model, setModel] = useState<CryptoStrategyModel>(DEFAULT_MODEL)
  const [accountsError, setAccountsError] = useState('')
  const [accountsLoading, setAccountsLoading] = useState(true)
  const [accountsRefreshing, setAccountsRefreshing] = useState(false)
  const [actionError, setActionError] = useState('')
  const [formError, setFormError] = useState('')
  const [form, setForm] = useState<FormState>(INITIAL_FORM)
  const [creationMode, setCreationMode] = useState<'single' | 'compare'>('single')
  const [submitting, setSubmitting] = useState(false)
  const [runningId, setRunningId] = useState<string | null>(null)
  const [accountActionId, setAccountActionId] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [details, setDetails] = useState<Record<string, CryptoStrategyAccountDetail>>({})
  const [detailLoading, setDetailLoading] = useState<Record<string, boolean>>({})
  const [detailErrors, setDetailErrors] = useState<Record<string, string>>({})
  const refreshAccountsRef = useRef<(() => Promise<void>) | null>(null)
  const detailRefreshRef = useRef<((id: string) => Promise<void>) | null>(null)
  const detailQueueRef = useRef<Promise<void>>(Promise.resolve())
  const panelAliveRef = useRef(false)
  const expandedIdRef = useRef<string | null>(null)
  const listRevision = useRef(0)
  const detailRevision = useRef<Record<string, number>>({})

  useEffect(() => {
    let alive = true
    cryptoApi.strategies()
      .then(result => {
        if (!alive) return
        setStrategies(result.strategies)
        setModel(result.model)
        setStrategyError('')
        setForm(current => result.strategies.some(item => item.id === current.strategy_id)
          ? current
          : { ...current, strategy_id: result.strategies[0]?.id ?? 'ema_trend' })
      })
      .catch(cause => { if (alive) setStrategyError(requestError(cause, '策略目录加载失败')) })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    let alive = true
    let inFlight = false
    let queuedRefresh = false
    let timer: number | undefined
    let loaded = false
    let pollSeconds = DEFAULT_MODEL.poll_seconds
    panelAliveRef.current = true

    const loadAccounts = async () => {
      if (timer !== undefined) {
        window.clearTimeout(timer)
        timer = undefined
      }
      if (!alive) return
      if (inFlight) {
        queuedRefresh = true
        return
      }
      inFlight = true
      const revisionAtStart = listRevision.current
      if (loaded) setAccountsRefreshing(true)
      else setAccountsLoading(true)
      try {
        const result = await cryptoApi.strategyAccounts()
        if (alive && revisionAtStart === listRevision.current) {
          setAccounts(result.accounts)
          setRuntimeRunning(result.runtime.running)
          setModel(result.model)
          setAccountsError('')
          loaded = true
          pollSeconds = result.runtime.poll_seconds || result.model.poll_seconds || DEFAULT_MODEL.poll_seconds
        }
        const expandedIdAtPoll = expandedIdRef.current
        if (alive && expandedIdAtPoll) await detailRefreshRef.current?.(expandedIdAtPoll)
      } catch (cause) {
        if (alive) setAccountsError(requestError(cause, '策略模拟账户加载失败'))
      } finally {
        inFlight = false
        if (!alive) return
        setAccountsLoading(false)
        setAccountsRefreshing(false)
        const nextDelay = queuedRefresh ? 0 : Math.max(5, Math.min(300, Number(pollSeconds) || 30)) * 1000
        queuedRefresh = false
        timer = window.setTimeout(() => { void loadAccounts() }, nextDelay)
      }
    }

    refreshAccountsRef.current = loadAccounts
    void loadAccounts()
    return () => {
      alive = false
      panelAliveRef.current = false
      if (timer !== undefined) window.clearTimeout(timer)
      if (refreshAccountsRef.current === loadAccounts) refreshAccountsRef.current = null
    }
  }, [])

  const currentAccounts = useMemo(() => (accounts ?? []).filter(isCurrentAccount), [accounts])
  const readOnlyAccounts = useMemo(() => (accounts ?? []).filter(account => !isCurrentAccount(account)), [accounts])
  const rankedAccounts = useMemo(() => [...currentAccounts].sort((left, right) => {
    const leftReady = !valuationPending(left)
    const rightReady = !valuationPending(right)
    if (!leftReady) return rightReady ? 1 : 0
    if (!rightReady) return -1
    if (left.return_pct == null) return right.return_pct == null ? 0 : 1
    if (right.return_pct == null) return -1
    return right.return_pct - left.return_pct
  }), [currentAccounts])

  const loadDetail = (id: string): Promise<void> => {
    const revision = (detailRevision.current[id] ?? 0) + 1
    detailRevision.current[id] = revision
    setDetailLoading(current => ({ ...current, [id]: true }))
    setDetailErrors(current => ({ ...current, [id]: '' }))
    const refresh = async () => {
      if (!panelAliveRef.current || expandedIdRef.current !== id || detailRevision.current[id] !== revision) return
      try {
        const result = await cryptoApi.strategyAccountDetail(id)
        if (panelAliveRef.current && expandedIdRef.current === id && detailRevision.current[id] === revision) {
          setDetails(current => ({ ...current, [id]: result }))
        }
      } catch (cause) {
        if (panelAliveRef.current && expandedIdRef.current === id && detailRevision.current[id] === revision) {
          setDetailErrors(current => ({ ...current, [id]: requestError(cause, '账户明细加载失败') }))
        }
      } finally {
        if (panelAliveRef.current && expandedIdRef.current === id && detailRevision.current[id] === revision) {
          setDetailLoading(current => ({ ...current, [id]: false }))
        }
      }
    }
    const queued = detailQueueRef.current.catch(() => undefined).then(refresh)
    detailQueueRef.current = queued
    return queued
  }
  detailRefreshRef.current = loadDetail

  const toggleDetail = (id: string) => {
    const previousId = expandedIdRef.current
    if (previousId === id) {
      expandedIdRef.current = null
      detailRevision.current[id] = (detailRevision.current[id] ?? 0) + 1
      setDetailLoading(current => ({ ...current, [id]: false }))
      setExpandedId(null)
      return
    }
    if (previousId) {
      detailRevision.current[previousId] = (detailRevision.current[previousId] ?? 0) + 1
      setDetailLoading(current => ({ ...current, [previousId]: false }))
    }
    expandedIdRef.current = id
    setExpandedId(id)
    void loadDetail(id)
  }

  const saveAccounts = (incoming: CryptoStrategyAccount[]) => {
    listRevision.current += 1
    setAccounts(current => mergeAccounts(current, incoming))
    setAccountsError('')
    void refreshAccountsRef.current?.()
  }

  const invalidateDetail = (id: string) => {
    detailRevision.current[id] = (detailRevision.current[id] ?? 0) + 1
    setDetails(current => {
      const next = { ...current }
      delete next[id]
      return next
    })
    if (expandedIdRef.current === id) void loadDetail(id)
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setFormError('')
    if (form.exchange !== 'bitget' || form.market !== 'usdm') {
      return setFormError('新建账户仅支持 Bitget USDT 本位合约。')
    }
    const initialCash = Number(form.initial_cash)
    const allocation = Number(form.allocation_pct)
    const stopLoss = Number(form.stop_loss_pct)
    const takeProfit = Number(form.take_profit_pct)
    if (!form.name.trim()) return setFormError('请填写账户名称。')
    if (!Number.isFinite(initialCash) || initialCash < 100 || initialCash > 10_000_000) return setFormError('初始资金须在 100 至 10,000,000 USDT 之间。')
    if (!Number.isFinite(allocation) || allocation <= 0 || allocation > 100) return setFormError('单次仓位须大于 0 且不超过 100%。')
    if (!Number.isFinite(stopLoss) || stopLoss <= 0 || stopLoss > 100) return setFormError('止损比例须大于 0 且不超过 100%。')
    if (!Number.isFinite(takeProfit) || takeProfit <= 0 || takeProfit > 100) return setFormError('止盈比例须大于 0 且不超过 100%。')
    if (creationMode === 'compare' && form.market !== 'usdm') return setFormError('杠杆对照组仅支持 U 本位合约。')

    let strategyParams: Record<string, number>
    if (form.strategy_id === 'ema_trend') {
      const fastPeriod = Number(form.fast_period)
      const slowPeriod = Number(form.slow_period)
      if (!Number.isInteger(fastPeriod) || fastPeriod < 5 || fastPeriod > 50) return setFormError('EMA 快线周期须为 5 至 50 的整数。')
      if (!Number.isInteger(slowPeriod) || slowPeriod < 20 || slowPeriod > 120) return setFormError('EMA 慢线周期须为 20 至 120 的整数。')
      if (fastPeriod >= slowPeriod) return setFormError('EMA 快线周期必须小于慢线周期。')
      strategyParams = { fast_period: fastPeriod, slow_period: slowPeriod }
    } else {
      const lookback = Number(form.lookback)
      if (!Number.isInteger(lookback) || lookback < 5 || lookback > 120) return setFormError('通道回看周期须为 5 至 120 的整数。')
      strategyParams = { lookback }
    }

    setSubmitting(true)
    setActionError('')
    const common = {
      name: form.name.trim(),
      exchange: 'bitget' as const,
      market: 'usdm' as const,
      symbol: form.symbol,
      strategy_id: form.strategy_id,
      strategy_params: strategyParams,
      interval: form.interval,
      initial_cash: initialCash,
      allocation_pct: allocation,
      stop_loss_pct: stopLoss,
      take_profit_pct: takeProfit,
      request_id: crypto.randomUUID(),
    }
    try {
      if (creationMode === 'compare') {
        const result = await cryptoApi.compareStrategyAccounts({ ...common, leverage_list: COMPARE_LEVERAGES })
        saveAccounts(result.accounts)
        const firstId = result.accounts[0]?.id ?? null
        expandedIdRef.current = firstId
        setExpandedId(firstId)
        if (firstId) void loadDetail(firstId)
      } else {
        const body: CryptoStrategyAccountCreate = {
          ...common,
          leverage: Number(form.leverage),
        }
        const result = await cryptoApi.createStrategyAccount(body)
        saveAccounts([result.account])
        expandedIdRef.current = result.account.id
        setExpandedId(result.account.id)
        void loadDetail(result.account.id)
      }
      setFormError('')
    } catch (cause) {
      setActionError(requestError(cause, '创建策略模拟账户失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const applyDraft = (draft: CryptoStrategyDraft) => {
    if (draft.exchange !== 'bitget' || draft.market !== 'usdm') {
      setFormError('Binance 历史草案仅供查看，不能填入账户创建表。')
      return
    }
    setCreationMode('single')
    setForm(current => ({
      ...current,
      name: draft.name,
      exchange: draft.exchange,
      market: draft.market,
      symbol: draft.symbol,
      strategy_id: draft.strategy_id,
      fast_period: 'fast_period' in draft.strategy_params ? String(draft.strategy_params.fast_period) : current.fast_period,
      slow_period: 'slow_period' in draft.strategy_params ? String(draft.strategy_params.slow_period) : current.slow_period,
      lookback: 'lookback' in draft.strategy_params ? String(draft.strategy_params.lookback) : current.lookback,
      interval: draft.interval,
      leverage: String(draft.market === 'spot' ? 1 : draft.leverage),
      initial_cash: String(draft.initial_cash),
      allocation_pct: String(draft.allocation_pct),
      stop_loss_pct: String(draft.stop_loss_pct),
      take_profit_pct: String(draft.take_profit_pct),
    }))
    setFormError('')
    setActionError('')
  }

  const toggleEnabled = async (account: CryptoStrategyAccount) => {
    if (!isCurrentAccount(account)) {
      setActionError(readOnlyAccountMessage(account))
      return
    }
    if (accountActionId !== null) return
    setAccountActionId(account.id)
    setActionError('')
    try {
      const result = await cryptoApi.setStrategyAccountEnabled(account.id, !account.enabled)
      saveAccounts([result.account])
      invalidateDetail(account.id)
    } catch (cause) {
      setActionError(requestError(cause, '更新策略启停状态失败'))
    } finally {
      setAccountActionId(null)
    }
  }

  const runAccount = async (account: CryptoStrategyAccount) => {
    if (!isCurrentAccount(account)) {
      setActionError(readOnlyAccountMessage(account))
      return
    }
    if (accountActionId !== null) return
    setAccountActionId(account.id)
    setRunningId(account.id)
    setActionError('')
    try {
      const result = await cryptoApi.runStrategyAccount(account.id)
      if (result.busy) {
        setActionError('后台正在检查其他账户，本次检查未执行；请稍后重试。')
        void refreshAccountsRef.current?.()
        return
      }
      if (result.stopped) {
        setActionError('后台检查已停止，本次检查未执行；请恢复服务后重试。')
        void refreshAccountsRef.current?.()
        return
      }
      saveAccounts([result.account])
      invalidateDetail(account.id)
    } catch (cause) {
      setActionError(requestError(cause, '策略账户检查失败'))
    } finally {
      setRunningId(null)
      setAccountActionId(null)
    }
  }

  const strategyOptions = strategies.length ? strategies : FALLBACK_STRATEGIES
  const renderedModel = model ?? DEFAULT_MODEL
  const renderAccountRows = (rows: CryptoStrategyAccount[], readOnly = false) => rows.map((account, index) => {
    const detail = details[account.id]
    const isExpanded = expandedId === account.id
    const pendingValuation = valuationMessage(account)
    const feeLabel = takerFeeLabel(account)
    return <Fragment key={account.id}>
      <tr className="border-b border-border/50 align-top hover:bg-elevated/30">
        <td className="px-3 py-3"><div className="font-medium text-foreground">{readOnly ? '历史记录' : `#${index + 1}`} · {account.name}</div><div className="mt-1 text-[11px] text-muted">{account.symbol} · {account.interval} · 初始 {formatMoney(account.initial_cash)} USDT</div></td>
        <td><div>{exchangeName(account.exchange)} · {account.market === 'spot' ? '现货' : 'U 本位'} · {accountName(account, strategies)}</div><div className="mt-1 text-[11px] text-muted">{strategyParametersLabel(account)} · 仓位 {formatPercent(account.allocation_pct)}</div>{feeLabel && <div className="mt-1 text-[10px] text-muted">{feeLabel}</div>}</td>
        <td>{account.leverage}x</td>
        <td className="font-mono">{pendingValuation ?? formatMoney(account.equity)}</td>
        <td className={`font-mono ${pendingValuation ? 'text-muted' : Number(account.total_pnl) > 0 ? 'text-accent' : Number(account.total_pnl) < 0 ? 'text-danger' : ''}`}>{pendingValuation ?? formatMoney(account.total_pnl)}</td>
        <td className={`font-mono ${pendingValuation ? 'text-muted' : Number(account.return_pct) > 0 ? 'text-accent' : Number(account.return_pct) < 0 ? 'text-danger' : ''}`}>{pendingValuation ?? formatPercent(account.return_pct)}</td>
        <td className="font-mono">{formatPercent(account.max_drawdown_pct)}</td>
        <td>{account.trade_count} / {account.liquidation_count}<div className="mt-1 text-[11px] text-muted">费 {formatMoney(account.fee_total, 4)} · 资费 {formatMoney(account.funding_total, 4)}</div></td>
        <td className="pr-3 py-2">
          {readOnly
            ? <div className="mb-2 space-y-1"><span className="rounded-full border border-border px-2 py-0.5 text-muted">历史只读</span><div className="text-[10px] text-muted">记录状态：{account.enabled ? '已启用' : '已暂停'} · {accountStatus(account.status)}</div></div>
            : <div className="mb-2 space-y-1"><span className={`rounded-full border px-2 py-0.5 ${account.enabled ? 'border-accent/40 bg-accent/10 text-accent' : 'border-border text-muted'}`}>{account.enabled ? '策略已启用' : '已暂停'}</span><div className="text-[10px] text-muted">账户状态：{accountStatus(account.status)}</div></div>}
          <div className="flex flex-wrap gap-1.5">
            {readOnly ? <>
              <button type="button" disabled title={readOnlyAccountMessage(account)} className="inline-flex cursor-not-allowed items-center gap-1 rounded-btn border border-border px-2 py-1 text-[11px] text-muted opacity-50">{account.enabled ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}{account.enabled ? '暂停' : '启用'}</button>
              <button type="button" disabled title={readOnlyAccountMessage(account)} className="cursor-not-allowed rounded-btn border border-border px-2 py-1 text-[11px] text-muted opacity-50">运行已禁用</button>
            </> : <>
              <button type="button" onClick={() => { void toggleEnabled(account) }} disabled={accountActionId !== null} className="inline-flex items-center gap-1 rounded-btn border border-border px-2 py-1 text-[11px] text-secondary hover:text-foreground disabled:opacity-50">
                {account.enabled ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}{account.enabled ? '暂停' : '启用'}
              </button>
              <button type="button" onClick={() => { void runAccount(account) }} disabled={accountActionId !== null} className="rounded-btn border border-border px-2 py-1 text-[11px] text-secondary disabled:opacity-50">{runningId === account.id ? '检查中…' : account.enabled ? '立即运行' : '检查持仓'}</button>
            </>}
            <button type="button" onClick={() => toggleDetail(account.id)} aria-expanded={isExpanded} className="inline-flex items-center gap-1 rounded-btn border border-border px-2 py-1 text-[11px] text-secondary">
              {isExpanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}{isExpanded ? '收起' : '明细'}
            </button>
            {!readOnly && onInspectAccount && <button type="button" onClick={() => onInspectAccount(account)} className="rounded-btn border border-accent/40 px-2 py-1 text-[11px] text-accent hover:bg-accent/10">在图表查看</button>}
          </div>
          {readOnly && <p className="mt-2 max-w-[260px] text-[10px] leading-relaxed text-muted">{readOnlyAccountMessage(account)}</p>}
          {(account.last_error || account.last_check_ms != null) && <div className={`mt-2 max-w-[260px] text-[10px] ${account.last_error ? 'text-danger' : 'text-muted'}`}>{account.last_error ? account.last_error : `最近检查 ${formatTime(account.last_check_ms)}`}</div>}
        </td>
      </tr>
      {isExpanded && <tr className="border-b border-border/50 bg-elevated/20"><td colSpan={9} className="p-3">
        {detailLoading[account.id] && !detail && <div className="flex items-center gap-2 py-5 text-xs text-muted"><Loader2 className="h-4 w-4 animate-spin" />正在加载账户净值和最近事件…</div>}
        {detailErrors[account.id] && <div className="rounded-btn border border-danger/40 bg-danger/10 p-3 text-xs text-danger">{detailErrors[account.id]}</div>}
        {detail && <div className="grid gap-3 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="space-y-3">
            {readOnly && <div className="rounded-btn border border-border bg-elevated/30 p-3 text-xs leading-relaxed text-secondary">{readOnlyAccountMessage(account)}以下账户净值、持仓和事件仅按已保存记录展示。</div>}
            <NavChart points={detail.nav} />
            <div className="grid grid-cols-2 gap-2 text-[11px] text-muted sm:grid-cols-4">
              <div className="rounded-btn border border-border p-2">现金 <b className="ml-1 font-mono text-foreground">{formatMoney(detail.account.cash)}</b></div>
              <div className="rounded-btn border border-border p-2">止损 / 止盈 <b className="ml-1 text-foreground">{formatPercent(detail.account.stop_loss_pct)} / {formatPercent(detail.account.take_profit_pct)}</b></div>
              <div className="rounded-btn border border-border p-2">最近信号 <b className="ml-1 text-foreground">{detail.account.last_signal || '—'}</b></div>
              <div className="rounded-btn border border-border p-2">信号 K 线 <b className="ml-1 text-foreground">{formatTime(detail.account.last_bar_time_ms)}</b></div>
            </div>
            <div className="rounded-btn border border-border p-3">
              <div className="text-xs font-semibold">当前持仓</div>
              {Object.entries(detail.account.positions ?? {}).length ? <div className="mt-2 space-y-1 text-[11px]">
                {Object.entries(detail.account.positions ?? {}).map(([symbol, position]) => {
                  const summary = positionSummary(position, detail.account.market)
                  return <div key={symbol} className="flex flex-wrap items-center justify-between gap-2"><span className="text-secondary">{symbol}</span><span className="text-muted">{summary.length ? summary.join(' · ') : '持仓字段暂不可用'}</span></div>
                })}
              </div> : <p className="mt-2 text-[11px] text-muted">暂无持仓。</p>}
            </div>
          </div>
          <div className="rounded-btn border border-border p-3">
            <div className="text-xs font-semibold">最近事件</div>
            {detail.trades.length ? <div className="mt-2 max-h-64 space-y-2 overflow-y-auto">
              {[...detail.trades].slice(-12).reverse().map(event => <div key={event.id} className="flex items-start justify-between gap-3 border-b border-border/50 pb-2 text-[11px] last:border-0">
                <div><div className="font-medium text-foreground">{eventLabel(event)}{event.symbol ? ` · ${event.symbol}` : ''}</div><div className="mt-0.5 text-muted">{eventTime(event)}{event.leverage ? ` · ${event.leverage}x` : ''}</div></div>
                <div className="text-right font-mono text-secondary">{event.quantity ? `${event.quantity} ` : ''}{event.price ? `@ ${formatMoney(event.price, 4)}` : ''}{event.fee ? <div className="text-muted">手续费 {formatMoney(event.fee, 4)}</div> : null}{event.funding_amount ? <div className="text-muted">资金费 {formatMoney(event.funding_amount, 4)}</div> : null}{event.realized_pnl ? <div>盈亏 {formatMoney(event.realized_pnl)}</div> : null}</div>
              </div>)}
            </div> : <p className="mt-3 text-xs text-muted">暂无成交、资金费或强平事件。</p>}
          </div>
        </div>}
      </td></tr>}
    </Fragment>
  })

  return <section className="space-y-4 rounded-card border border-border bg-surface p-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="flex items-center gap-2"><Activity className="h-4 w-4 text-accent" /><h2 className="text-sm font-semibold">策略自动模拟账户</h2></div>
        <p className="mt-1 text-xs text-muted">Bitget USDT 本位账户使用独立虚拟资金，可单独暂停、检查或做杠杆对照；Binance 账户只作为历史记录查看。</p>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded-full border px-2.5 py-1 ${runtimeRunning ? 'border-accent/40 bg-accent/10 text-accent' : 'border-border text-muted'}`}>
          后台轮询：{runtimeRunning ? '运行中' : '未运行'}
        </span>
        <button type="button" onClick={() => { void refreshAccountsRef.current?.() }} disabled={accountsRefreshing}
          className="flex items-center gap-1 rounded-btn border border-border px-2.5 py-1.5 text-secondary disabled:opacity-50">
          {accountsRefreshing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCcw className="h-3.5 w-3.5" />}
          {accountsRefreshing ? '刷新中' : '刷新账户'}
        </button>
      </div>
    </div>

    <div className="rounded-btn border border-amber-500/30 bg-amber-500/5 p-3 text-xs leading-relaxed text-amber-300">
      仅作研究模拟，新账户仅支持 Bitget USDT 本位合约，不连接真实账户或交易 Key。Bitget 成交手续费率使用公开合约规则返回的 Taker 费率；Binance 历史账户仅保留只读记录，不提供新建、启停或运行入口。费率尚未返回时显示“待获取公开手续费率”。Bitget 资金费使用交易所公布的实际费率及 1 分钟 MARK 价格开盘价估算标记价。维护保证金率 {formatRate(renderedModel.maintenance_margin_rate)}、强平费率 {formatRate(renderedModel.liquidation_fee_rate)} 是统一固定研究假设，不代表交易所实际保证金档位或撮合结果。策略只在所选 1 小时或 4 小时 K 线收盘后生成信号；后台约每 {model.poll_seconds || 30} 秒轮询，无需保持浏览器打开。暂停会停止策略开平仓，已有持仓仍继续进行资金费与强平核算。行情或资金费数据不可用、账户异常或补账期间，旧收益不代表最新状态。模拟收益不代表实盘盈利能力。
    </div>

    {(accountsError || strategyError || actionError) && <div className="space-y-1 rounded-btn border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
      {accountsError && <p>账户列表：{accountsError}</p>}
      {strategyError && <p>策略目录：{strategyError}</p>}
      {actionError && <p>操作失败：{actionError}</p>}
    </div>}

    <AiStrategyDraftPanel onApply={applyDraft} />

    <form onSubmit={submit} className="rounded-btn border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="账户创建方式">
          <button type="button" role="tab" aria-selected={creationMode === 'single'} onClick={() => setCreationMode('single')}
            className={`rounded-btn border px-3 py-1.5 text-xs ${creationMode === 'single' ? 'border-accent bg-accent/10 text-accent' : 'border-border text-secondary'}`}>单账户</button>
          <button type="button" role="tab" aria-selected={creationMode === 'compare'} disabled={form.market !== 'usdm'} onClick={() => setCreationMode('compare')}
            className={`rounded-btn border px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40 ${creationMode === 'compare' ? 'border-accent bg-accent/10 text-accent' : 'border-border text-secondary'}`}>杠杆对照组</button>
        </div>
        {creationMode === 'compare' && <span className="text-[11px] text-muted">一次原子创建 1x、5x、10x、20x 四个暂停账户</span>}
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-xs text-muted">账户名称<input aria-label="账户名称" required maxLength={80} value={form.name} onChange={event => setForm(current => ({ ...current, name: event.target.value }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground" /></label>
        <div className="text-xs text-muted">交易所<div className="mt-1 rounded-btn border border-border bg-base p-2 text-foreground">Bitget</div></div>
        <div className="text-xs text-muted">市场<div className="mt-1 rounded-btn border border-border bg-base p-2 text-foreground">USDT 本位合约</div></div>
        <label className="text-xs text-muted">交易对<select aria-label="策略交易对" value={form.symbol} onChange={event => setForm(current => ({ ...current, symbol: event.target.value as CryptoSymbol }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground">{SYMBOLS.map(item => <option key={item}>{item}</option>)}</select></label>
        <label className="text-xs text-muted">策略<select aria-label="策略类型" value={form.strategy_id} onChange={event => setForm(current => ({ ...current, strategy_id: event.target.value as CryptoStrategyId }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground">{strategyOptions.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        {form.strategy_id === 'ema_trend' ? <>
          <label className="text-xs text-muted">EMA 快线周期<input aria-label="EMA 快线周期" type="number" min="5" max="50" step="1" required value={form.fast_period} onChange={event => setForm(current => ({ ...current, fast_period: event.target.value }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
          <label className="text-xs text-muted">EMA 慢线周期<input aria-label="EMA 慢线周期" type="number" min="20" max="120" step="1" required value={form.slow_period} onChange={event => setForm(current => ({ ...current, slow_period: event.target.value }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
        </> : <label className="text-xs text-muted">通道回看周期<input aria-label="通道回看周期" type="number" min="5" max="120" step="1" required value={form.lookback} onChange={event => setForm(current => ({ ...current, lookback: event.target.value }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>}
        <label className="text-xs text-muted">信号周期<select aria-label="信号周期" value={form.interval} onChange={event => setForm(current => ({ ...current, interval: event.target.value as CryptoStrategyInterval }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground"><option value="1h">1 小时</option><option value="4h">4 小时</option></select></label>
        {form.market === 'usdm' && creationMode === 'single' && <label className="text-xs text-muted">杠杆<select aria-label="杠杆倍数" value={form.leverage} onChange={event => setForm(current => ({ ...current, leverage: event.target.value }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground">{LEVERAGES.map(item => <option key={item} value={item}>{item}x</option>)}</select></label>}
        <label className="text-xs text-muted">初始资金（USDT）<input aria-label="初始资金（USDT）" type="number" required min={100} max={10_000_000} step="any" value={form.initial_cash} onChange={event => setForm(current => ({ ...current, initial_cash: event.target.value }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
        <label className="text-xs text-muted">单次仓位（%）<input aria-label="单次仓位（%）" type="number" required min="0.000001" max={100} step="any" value={form.allocation_pct} onChange={event => setForm(current => ({ ...current, allocation_pct: event.target.value }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
        <label className="text-xs text-muted">止损（%）<input aria-label="止损（%）" type="number" required min="0.000001" max={100} step="any" value={form.stop_loss_pct} onChange={event => setForm(current => ({ ...current, stop_loss_pct: event.target.value }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
        <label className="text-xs text-muted">止盈（%）<input aria-label="止盈（%）" type="number" required min="0.000001" max={100} step="any" value={form.take_profit_pct} onChange={event => setForm(current => ({ ...current, take_profit_pct: event.target.value }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
      </div>
      {formError && <p className="mt-2 text-xs text-danger">{formError}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="submit" disabled={submitting} className="rounded-btn bg-accent px-3 py-2 text-xs font-medium text-white disabled:opacity-50">{submitting ? '创建中…' : creationMode === 'compare' ? '创建四个暂停账户' : '创建暂停账户'}</button>
        <span className="text-[11px] text-muted">新账户默认暂停；创建后需手动启用策略。服务每 {model.poll_seconds || 30} 秒轮询一次。</span>
      </div>
    </form>

    <div className="overflow-x-auto rounded-btn border border-border">
      <table className="w-full min-w-[1060px] text-left text-xs">
        <thead className="border-b border-border bg-elevated/40 text-muted"><tr>
          <th className="px-3 py-2">收益排名 / 账户</th><th>市场与策略</th><th>杠杆</th><th>权益</th><th>总盈亏</th><th>收益率</th><th>最大回撤</th><th>成交 / 强平</th><th className="pr-3">状态与操作</th>
        </tr></thead>
        <tbody>
          {accountsLoading && accounts === null && <tr><td colSpan={9} className="px-3 py-8 text-center text-muted"><span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" />正在加载策略账户…</span></td></tr>}
          {!accountsLoading && accounts === null && accountsError && <tr><td colSpan={9} className="px-3 py-8 text-center text-danger">策略账户列表加载失败，请稍后刷新。</td></tr>}
          {!accountsLoading && accounts !== null && rankedAccounts.length === 0 && <tr><td colSpan={9} className="px-3 py-8 text-center text-muted">暂无 Bitget USDT 本位合约账户。创建的账户会保存在后端，初始状态为暂停；历史账户可在下方展开查看。</td></tr>}
          {renderAccountRows(rankedAccounts)}
        </tbody>
      </table>
    </div>
    {readOnlyAccounts.length > 0 && <details className="rounded-btn border border-border">
      <summary className="cursor-pointer list-none px-3 py-2 text-xs font-medium text-secondary">历史只读账户（{readOnlyAccounts.length}） · Binance {readOnlyAccounts.filter(account => account.exchange === 'binance').length} 个</summary>
      <div className="space-y-2 border-t border-border p-3">
        <p className="text-[11px] leading-relaxed text-muted">历史账户不参与当前账户收益排名。Binance 账户只保留已保存数据供查看；启用、暂停和运行按钮禁用，不会发送相应操作或行情请求。</p>
        <div className="overflow-x-auto rounded-btn border border-border">
          <table className="w-full min-w-[1060px] text-left text-xs">
            <thead className="border-b border-border bg-elevated/40 text-muted"><tr>
              <th className="px-3 py-2">历史账户</th><th>市场与策略</th><th>杠杆</th><th>权益</th><th>总盈亏</th><th>收益率</th><th>最大回撤</th><th>成交 / 强平</th><th className="pr-3">只读状态</th>
            </tr></thead>
            <tbody>{renderAccountRows(readOnlyAccounts, true)}</tbody>
          </table>
        </div>
      </div>
    </details>}
    <div className="flex flex-wrap justify-between gap-2 text-[11px] text-muted">
      <span>收益率用于账户排名；账户之间虚拟资金、持仓和费用独立。</span>
      <span>模型滑点：{renderedModel.slippage_bps} bps · 轮询间隔：{renderedModel.poll_seconds}s</span>
    </div>
  </section>
}
