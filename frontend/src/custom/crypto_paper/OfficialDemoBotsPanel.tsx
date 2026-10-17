import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, LoaderCircle, Pause, Play, RefreshCcw, RotateCcw, ShieldCheck } from 'lucide-react'
import { QK } from '@/lib/queryKeys'
import { JevosStrategyShadow, useJevosStatus } from '@/custom/jevos/components'
import { demoBotsApi, type DemoBot, type DemoBotOrder, type DemoBotsPreflight } from './demo-bots-client'

const EXCHANGE = 'okx' as const
const BUTTON_CLASS = 'inline-flex min-h-9 items-center justify-center gap-2 rounded-btn border border-border px-3 py-2 text-xs font-medium text-secondary transition-colors hover:border-accent hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50'
const ENABLE_BUTTON_CLASS = 'inline-flex min-h-9 items-center justify-center gap-2 rounded-btn bg-accent px-3 py-2 text-xs font-semibold text-white transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50'
const PAUSE_BUTTON_CLASS = 'inline-flex min-h-9 items-center justify-center gap-2 rounded-btn border border-amber-500/35 px-3 py-2 text-xs font-medium text-amber-300 transition-colors hover:bg-amber-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 disabled:cursor-not-allowed disabled:opacity-50'

const SAFE_REASONS = new Set([
  '已准备；连接欧易官方模拟后可启用',
  '已暂停；不会自动平仓，已有持仓和挂单需继续核对',
  '官方模拟已启用，等待已收盘K线与交易条件',
  '官方委托尚未完整成交，等待对账；不重复发单',
  '官方订单未成交而结束，策略已暂停；不会重发',
  '官方完整成交已对账，等待后续规则',
  '意图已保存，等待官方模拟回执',
  '官方已接受，等待完整成交对账',
  '请求结果待核对，策略已暂停；只查单不自动重发',
  '发送前已暂停，本笔未发送',
  '规则未产生新动作，等待下一根已收盘K线或退出条件',
  '行情、连接或官方配置预检暂未通过；未重发订单，等待下一轮核对',
  '当前未启用执行；重启或连接变化后需重新连接并启用，原成交保留',
  '请先连接欧易官方模拟，再重新启用策略；原成交与待对账订单保留',
  '服务重启后未启用执行，请重新启用；原成交保留',
  '官方模拟连接已变化，请核对后重新启用；原成交保留',
  '请先在OKX模拟盘设为合约账户、单向持仓模式',
  '请先在OKX官方模拟盘将该合约设为逐仓1倍',
  '请先在OKX官方模拟盘将该合约设为逐仓10倍',
  '10U额度不足当前最小交易单位，本轮跳过，不提高额度',
  '官方可用余额不足开仓及费用余量',
  '策略日亏1U或20%回撤门槛已触发，停止新增开仓',
  '策略K线未收盘、过期或历史不足，本轮未发单',
  '该标的已有活动委托，等待处理；未新增请求',
  '官方报价已过期，未发单',
  '预检期间价格变化超过0.5%，等待下一轮',
  '官方持仓与本策略记录不一致，不接管手动仓位',
  '官方仓位数量、方向或保证金配置已变化，请人工核对',
  '订单部分成交后结束，请在官方核对仓位；本策略保持待对账',
  '请先暂停原单策略官方模拟执行，避免共享仓位冲突',
  '官方模拟USDT可用余额不足策略额度总和',
  '策略存在待对账订单，请先对账；不会重复发单',
  '策略标的存在官方活动委托，请先处理',
  '已有策略仓位不属于当前模拟连接',
  '启用期间连接已变化或已暂停，本次未启用',
])

function knownReason(reason: string): string | null {
  if (SAFE_REASONS.has(reason)) return reason
  const match = reason.match(/^(.+) \[demo-error:(exchange_code:[0-9]{1,8}|http_status:[1-5][0-9]{2}|network|invalid_response|exchange_error)\]$/)
  if (!match || !SAFE_REASONS.has(match[1])) return null
  const [category, code] = match[2].split(':')
  const detail = category === 'exchange_code' ? `交易所错误码 ${code}`
    : category === 'http_status' ? `HTTP ${code}`
      : category === 'network' ? '网络请求失败'
        : category === 'invalid_response' ? '返回格式异常' : '交易所返回错误'
  return `${match[1]}（${detail}）`
}

function actionError(error: unknown, fallback: string) {
  return error instanceof Error ? knownReason(error.message) ?? fallback : fallback
}

function safeReason(reason: string) {
  return knownReason(reason) ?? '策略状态需要核对；请检查欧易官方模拟配置和未完成委托。'
}

function formatTime(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value) || value <= 0) return '尚未更新'
  return new Date(value).toLocaleString('zh-CN')
}

function formatNumber(value: number | string | null | undefined, digits = 4) {
  if (value == null || value === '') return '—'
  const numeric = Number(value)
  return Number.isFinite(numeric)
    ? numeric.toLocaleString('zh-CN', { maximumFractionDigits: digits })
    : '—'
}

function formatUsdt(value: number | string | null | undefined) {
  const formatted = formatNumber(value, 4)
  return formatted === '—' ? formatted : `${formatted} USDT`
}

function formatPercent(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? '—' : `${formatNumber(value, 2)}%`
}

function amountTone(value: string | null | undefined) {
  const amount = Number(value)
  if (!Number.isFinite(amount) || amount === 0) return 'text-foreground'
  return amount > 0 ? 'text-accent' : 'text-danger'
}

function botStateLabel(bot: DemoBot) {
  if (bot.status === 'reconciling') return '待对账'
  if (bot.status === 'blocked') return '预检受阻'
  if (bot.armed) return '运行中'
  if (!bot.enabled) return '已暂停'
  return '已配置，当前未运行'
}

function signalLabel(signal: string | null) {
  if (signal === 'long') return '多头'
  if (signal === 'short') return '空头'
  if (signal === 'flat') return '空仓'
  if (signal === 'hold') return '保持'
  return signal ? '已记录' : '暂无'
}

function actionLabel(action: string) {
  if (action === 'open_long') return '开多'
  if (action === 'open_short') return '开空'
  if (action === 'close_long') return '平多'
  if (action === 'close_short') return '平空'
  return '模拟委托'
}

function orderStateLabel(state: string) {
  if (state === 'reviewed_not_found') return '人工核对未找到'
  if (state === 'filled' || state === 'confirmed') return '已成交'
  if (state === 'rejected') return '已拒绝'
  if (state === 'canceled' || state === 'cancelled' || state === 'mmp_canceled') return '已撤销'
  return '待核对'
}

function isUnresolved(order: DemoBotOrder) {
  return !['filled', 'confirmed', 'rejected', 'canceled', 'cancelled', 'closed', 'mmp_canceled', 'reviewed_not_found'].includes(order.state.toLowerCase())
}

function executionStateClass(bot: DemoBot) {
  if (bot.armed) return 'border-accent/30 bg-accent/10 text-accent'
  if (bot.status === 'blocked' || bot.status === 'reconciling') return 'border-amber-500/35 bg-amber-500/10 text-amber-300'
  return 'border-border bg-base text-secondary'
}

function PositionSummary({ bot }: { bot: DemoBot }) {
  if (bot.position_verified !== true) return <div className="mt-2 text-sm font-medium text-amber-300">官方仓位待核对；浮盈和额度权益暂不计算</div>
  if (!bot.position) return <div className="mt-2 text-sm font-medium text-secondary">当前空仓</div>
  const side = bot.position.side === 'long' ? '多头' : bot.position.side === 'short' ? '空头' : '方向待核对'
  return <div className="mt-2 space-y-1 text-sm text-foreground">
    <div>{side} · {formatNumber(bot.position.quantity, 8)} 张</div>
    <div className="text-[11px] text-muted">入场价 {formatNumber(bot.position.entry_price, 8)} · 名义金额 {formatUsdt(bot.position.notional)}</div>
  </div>
}

function OrderHistory({ orders }: { orders: DemoBotOrder[] }) {
  if (orders.length === 0) return null
  const recent = orders.slice(-4).reverse()
  return <div className="mt-3 overflow-x-auto rounded-btn border border-border">
    <table className="w-full min-w-[580px] text-left text-[11px]">
      <thead className="bg-elevated text-muted"><tr>
        {['时间', '动作', '状态', '成交数量（张）', '均价', '手续费'].map(label => <th key={label} className="whitespace-nowrap px-2.5 py-2 font-medium">{label}</th>)}
      </tr></thead>
      <tbody className="divide-y divide-border">
        {recent.map(order => <tr key={`${order.client_id}-${order.created_at_ms}`} className="text-secondary">
          <td className="whitespace-nowrap px-2.5 py-2">{formatTime(order.created_at_ms)}</td>
          <td className="whitespace-nowrap px-2.5 py-2">{actionLabel(order.action)}</td>
          <td className="whitespace-nowrap px-2.5 py-2">{orderStateLabel(order.state)}</td>
          <td className="whitespace-nowrap px-2.5 py-2">{formatNumber(order.filled_quantity, 8)}</td>
          <td className="whitespace-nowrap px-2.5 py-2">{formatNumber(order.avg_price, 8)}</td>
          <td className="whitespace-nowrap px-2.5 py-2">{formatUsdt(order.fee_usdt)}</td>
        </tr>)}
      </tbody>
    </table>
  </div>
}

function BotCard({ bot, connectionReady, legacyArmed, confirmDemo, pending, actionPending, onToggle, onReconcile }: {
  bot: DemoBot
  connectionReady: boolean
  legacyArmed: boolean
  confirmDemo: boolean
  pending: boolean
  actionPending: boolean
  onToggle: (bot: DemoBot, enabled: boolean) => void
  onReconcile: (bot: DemoBot) => void
}) {
  const hasUnresolved = bot.orders.some(isUnresolved)
  const canEnable = connectionReady && !legacyArmed && confirmDemo && !hasUnresolved && !pending
  const hasEnableIntent = bot.enabled || bot.armed
  const closedTrades = bot.metrics.closed_trades
  const sampleIsSmall = closedTrades < 20
  const reason = safeReason(bot.reason)
  const stateLabel = botStateLabel(bot)

  return <article className="flex min-w-0 flex-col rounded-card border border-border bg-surface p-3 md:p-4">
    <div className="flex flex-wrap items-start gap-2">
      <div className="min-w-0 flex-1">
        <h3 className="break-words text-sm font-semibold text-foreground">{bot.name}</h3>
        <p className="mt-1 text-xs text-secondary">{bot.symbol} <span className="text-muted">· {bot.interval}</span></p>
      </div>
      <span className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium ${executionStateClass(bot)}`}>
        {bot.armed ? <CheckCircle2 className="h-3.5 w-3.5" /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}{stateLabel}
      </span>
    </div>

    <p className="mt-3 rounded-btn border border-border bg-base p-2.5 text-xs leading-relaxed text-secondary">
      {bot.description || '规则说明暂不可用。'}
    </p>

    <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
      <div className="rounded-btn border border-border bg-base p-2.5">
        <div className="text-muted">独立保证金预算</div><div className="mt-1 font-semibold text-foreground">{formatUsdt(bot.budget_usdt)}</div>
      </div>
      <div className="rounded-btn border border-border bg-base p-2.5">
        <div className="text-muted">新开仓名义上限</div><div className="mt-1 font-semibold text-foreground">{formatUsdt(bot.max_notional)}</div>
      </div>
    </div>
    <p className="mt-2 text-[11px] text-muted">只做多 · 逐仓 10× · IOC 保护限价 · 标的价格止损 0.8% / 止盈 1.2% / 最长持仓 1 小时</p>

    <div className="mt-3 rounded-btn border border-border bg-base p-3">
      <div className="text-[11px] font-medium text-muted">策略持仓</div>
      <PositionSummary bot={bot} />
    </div>

    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
      <Metric label="实际成交净盈亏" value={formatUsdt(bot.metrics.realized_pnl_usdt)} valueClass={amountTone(bot.metrics.realized_pnl_usdt)} />
      <Metric label="累计手续费" value={formatUsdt(bot.metrics.fees_usdt)} />
      <Metric label="浮动盈亏" value={formatUsdt(bot.metrics.open_pnl_usdt)} valueClass={amountTone(bot.metrics.open_pnl_usdt ?? undefined)} />
      <Metric label="胜率" value={bot.metrics.win_rate_pct == null ? '暂无' : formatPercent(bot.metrics.win_rate_pct)} />
      <Metric label="胜场 / 已平仓" value={`${bot.metrics.wins} / ${closedTrades}`} />
      <Metric label="最大回撤" value={formatPercent(bot.metrics.max_drawdown_pct)} />
      <Metric label="额度权益" value={formatUsdt(bot.metrics.allocation_equity_usdt)} />
    </div>
    {sampleIsSmall && <p className="mt-2 text-[11px] text-amber-300">样本较少：仅 {closedTrades} 笔已平仓成交，胜率可能不稳定。</p>}

    <div className="mt-3 space-y-1.5 rounded-btn border border-border bg-base p-3 text-[11px] leading-relaxed">
      <div><span className="text-muted">最新规则信号：</span><span className="text-secondary">{signalLabel(bot.last_signal)}</span></div>
      <div><span className="text-muted">执行原因：</span><span className="text-secondary">{reason}</span></div>
      <div><span className="text-muted">最近检查：</span><span className="text-secondary">{formatTime(bot.last_check_ms)}</span></div>
      <div><span className="text-muted">最近已收盘K线：</span><span className="text-secondary">{formatTime(bot.last_bar_ms)}</span></div>
    </div>

    {bot.orders.length > 0 && <div className="mt-3">
      <div className="text-[11px] font-medium text-muted">最近模拟委托</div>
      <OrderHistory orders={bot.orders} />
    </div>}

    <div className="mt-auto pt-4">
      {hasUnresolved && <p className="mb-2 rounded-btn border border-amber-500/30 bg-amber-500/5 p-2.5 text-[11px] leading-relaxed text-amber-300">
        存在未完成或待核对委托。先核对欧易官方状态；暂停不会撤单或平仓。
      </p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => onToggle(bot, !hasEnableIntent)} disabled={pending || (hasEnableIntent ? false : !canEnable)} className={hasEnableIntent ? PAUSE_BUTTON_CLASS : ENABLE_BUTTON_CLASS}>
          {actionPending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : hasEnableIntent ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          {actionPending ? '处理中…' : hasEnableIntent ? '暂停此策略' : '启用此策略（官方模拟）'}
        </button>
        {hasUnresolved && <button type="button" onClick={() => onReconcile(bot)} disabled={pending || !connectionReady} className={BUTTON_CLASS}>
          {actionPending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}仅对账
        </button>}
      </div>
      {hasUnresolved && !connectionReady && <p className="mt-2 text-[11px] text-muted">连接欧易官方模拟账户后才能查询委托状态。</p>}
      {!connectionReady && !hasEnableIntent && <p className="mt-2 text-[11px] text-muted">启用前请先连接欧易官方模拟账户。</p>}
      {legacyArmed && !hasEnableIntent && <p className="mt-2 text-[11px] text-amber-300">旧版单策略仍在运行；请先在上方官方执行区暂停，二者不能同时运行。</p>}
      {!confirmDemo && !hasEnableIntent && connectionReady && !legacyArmed && !hasUnresolved && <p className="mt-2 text-[11px] text-muted">勾选页面上方确认项后可启用。</p>}
    </div>
  </article>
}

function Metric({ label, value, valueClass = 'text-foreground' }: { label: string; value: string; valueClass?: string }) {
  return <div className="min-w-0 rounded-btn border border-border bg-base p-2.5">
    <div className="truncate text-[10px] text-muted">{label}</div>
    <div className={`mt-1 break-words text-xs font-semibold ${valueClass}`}>{value}</div>
  </div>
}

export function OfficialDemoBotsPanel() {
  const queryClient = useQueryClient()
  const jevos = useJevosStatus()
  const [confirmDemo, setConfirmDemo] = useState(false)
  const [pendingAction, setPendingAction] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [preflight, setPreflight] = useState<DemoBotsPreflight | null>(null)
  const queryKey = QK.demoBots(EXCHANGE)
  const statusQuery = useQuery({
    queryKey,
    queryFn: ({ signal }) => demoBotsApi.status(EXCHANGE, signal),
    retry: false,
    staleTime: 0,
    refetchInterval: 10_000,
  })
  const status = statusQuery.data
  const bots = status?.bots ?? []
  const hasPendingOrders = bots.some(bot => bot.orders.some(isUnresolved))
  const canEnable = Boolean(status?.connection_ready && !status.legacy_armed && confirmDemo && bots.length > 0 && !hasPendingOrders)
  const canPause = bots.some(bot => bot.enabled || bot.armed)
  const invalidateStatus = () => queryClient.invalidateQueries({ queryKey, exact: true })

  const toggleBot = async (bot: DemoBot, enabled: boolean) => {
    const canEnableBot = Boolean(status?.connection_ready && !status.legacy_armed && confirmDemo
      && !bot.orders.some(isUnresolved))
    if (pendingAction || (enabled && !canEnableBot)) return
    setPendingAction(bot.id)
    setNotice(null)
    setError(null)
    try {
      await demoBotsApi.setBotEnabled(EXCHANGE, bot.id, { enabled, confirm_demo: enabled })
      setNotice(enabled ? `${bot.name} 已请求启动官方模拟。` : `${bot.name} 已请求暂停；不会自动撤单或平仓。`)
      if (enabled) setConfirmDemo(false)
    } catch (failure) {
      setError(actionError(failure, enabled
        ? '策略启用失败，请检查官方模拟配置；未完整成交时请执行对账。'
        : '策略暂停失败，请稍后刷新状态并核对官方委托。'))
    } finally {
      await invalidateStatus()
      setPendingAction(null)
    }
  }

  const toggleAll = async (enabled: boolean) => {
    if (pendingAction || (enabled && !canEnable)) return
    setPendingAction('all')
    setNotice(null)
    setError(null)
    try {
      await demoBotsApi.setEnabled(EXCHANGE, { enabled, confirm_demo: enabled })
      setNotice(enabled ? '已请求启动全部策略的官方模拟。' : '已请求暂停全部策略；不会自动撤单或平仓。')
      if (enabled) setConfirmDemo(false)
    } catch (failure) {
      setError(actionError(failure, enabled
        ? '批量启用失败，请检查官方模拟配置；未完整成交时请执行对账。'
        : '批量暂停失败，请稍后刷新状态并核对官方委托。'))
    } finally {
      await invalidateStatus()
      setPendingAction(null)
    }
  }

  const createPresets = async () => {
    if (pendingAction) return
    setPendingAction('presets')
    setNotice(null)
    setError(null)
    try {
      await demoBotsApi.createPresets(EXCHANGE)
      setNotice('已创建默认停用的策略预设；请检查规则和账户状态后再启用。')
    } catch {
      setError('创建策略预设失败，请稍后刷新重试。')
    } finally {
      await invalidateStatus()
      setPendingAction(null)
    }
  }

  const checkSetup = async () => {
    if (pendingAction || !status?.connection_ready) return
    setPendingAction('preflight')
    setError(null)
    setPreflight(null)
    try {
      setPreflight(await demoBotsApi.preflight(EXCHANGE))
    } catch (failure) {
      setError(actionError(failure, '运行条件检查失败，请检查欧易官方模拟连接后重试。'))
    } finally {
      setPendingAction(null)
    }
  }

  const reconcileBot = async (bot: DemoBot) => {
    if (pendingAction || !status?.connection_ready) return
    setPendingAction(`reconcile:${bot.id}`)
    setNotice(null)
    setError(null)
    try {
      await demoBotsApi.reconcile(EXCHANGE, bot.id)
      setNotice(`${bot.name} 对账请求已完成；请以刷新后的官方状态核对。`)
    } catch {
      setError('委托对账失败，请核对欧易官方委托并稍后重试。')
    } finally {
      await invalidateStatus()
      setPendingAction(null)
    }
  }

  return <section id="okx-strategy-bots" className="scroll-mt-4 space-y-3 rounded-card border border-border bg-surface p-3 md:p-4">
    <div className="flex flex-wrap items-start gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2 pl-8 md:pl-0">
          <h2 className="text-sm font-semibold text-foreground">欧易官方模拟 · 多策略面板</h2>
          <span className="rounded-full border border-amber-500/35 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-300">仅 Demo</span>
        </div>
        <p className="mt-1 text-xs leading-relaxed text-muted">三种策略各有 10 USDT 独立保证金预算与业绩统计，所有策略共用同一个欧易官方模拟余额，不是三个独立子账户。</p>
      </div>
      <button type="button" onClick={() => { void statusQuery.refetch() }} disabled={statusQuery.isFetching || Boolean(pendingAction)} className={BUTTON_CLASS}>
        {statusQuery.isFetching ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RefreshCcw className="h-3.5 w-3.5" />}刷新状态
      </button>
    </div>

    <div className="flex items-start gap-2 rounded-btn border border-amber-500/30 bg-amber-500/5 p-3 text-xs leading-relaxed text-amber-200">
      <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
      <p>每套 10 USDT 保证金预算，逐仓 10×，新开仓名义金额最多 90 USDT（保证金最多约 9 USDT）。0.8% 标的价格止损约为 8% 保证金亏损，另加费用和滑点；轮询退出无法保证亏损上限。暂停不会自动平仓或撤单，未完整成交时请先“仅对账”。</p>
    </div>

    <div className="rounded-btn border border-border bg-base p-3 text-[11px] leading-relaxed text-muted">
      统计只从实际模拟成交后向前累计，不包含回测或补造历史数据。资金费尚未分摊，未计入策略统计。
    </div>

    {statusQuery.isPending && !status && <div role="status" className="rounded-btn border border-dashed border-border px-4 py-8 text-center text-sm text-muted">正在读取欧易官方模拟策略状态…</div>}
    {statusQuery.isError && !status && <div role="alert" className="rounded-btn border border-danger/30 bg-danger/5 p-3 text-xs text-danger">策略状态暂不可用。请稍后刷新，并检查欧易官方模拟账户连接。</div>}
    {statusQuery.isError && status && <div role="status" className="rounded-btn border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-300">状态刷新暂不可用；当前保留上次成功读取的数据。</div>}

    {status && <>
      {!status.connection_ready && <div role="status" className="flex items-start gap-2 rounded-btn border border-amber-500/35 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-300">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><p>尚未连接欧易官方模拟账户。连接并完成官方合约账户配置后，才能启用或对账策略。</p>
      </div>}
      {status.legacy_armed && <div role="status" className="flex items-start gap-2 rounded-btn border border-amber-500/35 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-300">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><p>旧版单策略执行仍在运行，与此策略组互斥。请先在上方旧版官方模拟执行面板暂停它。</p>
      </div>}
      {hasPendingOrders && <div role="status" className="flex items-start gap-2 rounded-btn border border-amber-500/35 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-300">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><p>策略组有未完成或待核对委托；启用前请完成官方对账。不会自动重发委托。</p>
      </div>}

      <label className="flex items-start gap-2 rounded-btn border border-border bg-base p-3 text-xs text-secondary">
        <input type="checkbox" aria-label="确认仅使用欧易官方模拟" checked={confirmDemo} onChange={event => setConfirmDemo(event.target.checked)} disabled={Boolean(pendingAction)} className="mt-0.5 accent-[var(--color-accent)]" />
        <span><span className="font-medium text-foreground">确认仅使用欧易官方模拟环境，不涉及实盘资金</span><span className="mt-1 block text-[11px] leading-relaxed text-muted">此确认仅控制本页启动按钮；初始未勾选。每次启动成功后会清除此确认。</span></span>
      </label>

      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => { void checkSetup() }} disabled={!status.connection_ready || Boolean(pendingAction)} className={BUTTON_CLASS}>
          {pendingAction === 'preflight' ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}检查运行条件（不下单）
        </button>
        <button type="button" onClick={() => { void toggleAll(true) }} disabled={!canEnable || Boolean(pendingAction) || bots.every(bot => bot.armed)} className={ENABLE_BUTTON_CLASS}>
          {pendingAction === 'all' ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}全部启用（官方模拟）
        </button>
        <button type="button" onClick={() => { void toggleAll(false) }} disabled={!canPause || Boolean(pendingAction)} className={PAUSE_BUTTON_CLASS}>
          {pendingAction === 'all' ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Pause className="h-3.5 w-3.5" />}全部暂停
        </button>
        <span className="self-center text-[11px] text-muted">当前读取周期 10 秒 · 暂停仅停止策略执行，不会自动平仓。</span>
      </div>

      {preflight && <div role="region" aria-label="官方模拟运行条件" className="space-y-3 rounded-btn border border-border bg-base p-3 text-xs">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <strong className={preflight.ready ? 'text-accent' : 'text-amber-300'}>{preflight.ready ? '配置检查通过，尚未启用策略' : '以下条件尚未满足，未启用或下单'}</strong>
          <span className="text-muted">检查时间：{formatTime(preflight.checked_at_ms)}</span>
        </div>
        {[{ id: 'account', name: '账户共用配置', checks: preflight.checks }, ...preflight.bots].map(group => <div key={group.id}>
          <h4 className="mb-1.5 font-medium text-foreground">{group.name}</h4>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{group.checks.map(check => <div key={check.label} className={`rounded border p-2 ${check.passed ? 'border-border text-secondary' : 'border-amber-500/35 text-amber-300'}`}>
            <div>{check.passed ? '✓' : '待处理'} · {check.label}</div>
            <div className="mt-1">当前：{check.actual}</div><div className="mt-1 text-muted">要求：{check.required}</div>
          </div>)}</div>
        </div>)}
        <p className="leading-relaxed text-muted">在欧易 App 的模拟交易设置中核对账户模式和单向持仓；分别进入 BTC、ETH、SOL 的 USDT 永续，将逐仓杠杆设为 10 倍，再检查。本检查仅查询，不修改官方设置、撤单、平仓或启动策略；实际启用时仍会重新预检。</p>
      </div>}

      {notice && <p role="status" className="rounded-btn border border-accent/25 bg-accent/5 p-3 text-xs text-accent">{notice}</p>}
      {error && <p role="alert" className="rounded-btn border border-danger/25 bg-danger/5 p-3 text-xs text-danger">{error}</p>}

      {bots.length === 0
        ? <div className="rounded-btn border border-dashed border-border px-4 py-8 text-center">
          <p className="text-sm text-secondary">尚未创建策略预设。</p>
          <p className="mt-1 text-xs text-muted">创建操作只保存三项默认停用的配置，不会连接账户、启用策略或发出委托。</p>
          <button type="button" onClick={() => { void createPresets() }} disabled={Boolean(pendingAction)} className={`${ENABLE_BUTTON_CLASS} mt-4`}>
            {pendingAction === 'presets' ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : null}创建三个默认策略预设
          </button>
        </div>
        : <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
          {bots.map(bot => <BotCard
            key={bot.id}
            bot={bot}
            connectionReady={status.connection_ready}
            legacyArmed={status.legacy_armed}
            confirmDemo={confirmDemo}
            pending={Boolean(pendingAction)}
            actionPending={pendingAction === bot.id || pendingAction === `reconcile:${bot.id}`}
            onToggle={(target, enabled) => { void toggleBot(target, enabled) }}
            onReconcile={target => { void reconcileBot(target) }}
          />)}
        </div>}
      <JevosStrategyShadow status={jevos.data} isLoading={jevos.isPending} isError={jevos.isError}
        bots={bots.map(bot => ({ id: bot.id, name: bot.name, last_bar_ms: bot.last_bar_ms, last_signal: bot.last_signal }))} />
    </>}
  </section>
}
