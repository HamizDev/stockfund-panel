import { useMemo, useState } from 'react'
import type { PaperCompareRow } from '@/lib/api'
import { usePaperInstrumentNames } from '@/lib/usePaperInstrumentNames'
import { PaperInstrumentLabel } from './PaperInstrumentLabel'

type ShortTermState = NonNullable<PaperCompareRow['shortterm']>
type ShortTermCandidate = ShortTermState['symbols'][string]
type ReasonCategory = '数据等待' | '费用门槛' | '信号等待' | '持仓与资金' | '已有订单' | '未知原因'

const REASON_CATEGORIES: ReasonCategory[] = [
  '数据等待',
  '费用门槛',
  '信号等待',
  '持仓与资金',
  '已有订单',
  '未知原因',
]

const ACTION_LABELS: Record<string, string> = {
  seed: '初始建仓',
  t_sell: '做 T 卖出',
  t_buy: '做 T 回补',
  t0_entry: '日内回归买入',
  t0_exit: '日内退出',
}

function classifyReason(reason?: string): ReasonCategory {
  const value = reason?.trim() ?? ''
  if (!value) return '未知原因'
  if (/已建单|待买单|待卖单|已有.{0,3}单|后续有效行情撮合|订单/.test(value)) return '已有订单'
  if (/费用门槛|入场门槛|双边费用|手续费|佣金|滑点门槛|成本门槛/.test(value)) return '费用门槛'
  if (/可卖|底仓|持仓|持有|预算|现金|限仓|资金|一手/.test(value)) return '持仓与资金'
  if (/实时价低于|等待[^。；]*(?:回升|回落|趋势\/回归信号|趋势\/回归)|趋势已满足|偏离已满足|已完成波段|停止新开仓/.test(value)) return '信号等待'
  if (/行情|分钟|数据|日线|均线|来源|字段|时标|缺口|过期|待同步|读取失败|待核对/.test(value)) return '数据等待'
  if (/等待|趋势|回升|回落|信号|波段/.test(value)) return '信号等待'
  return '未知原因'
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function ratio(value: number): string {
  return (value * 100).toFixed(2) + '%'
}

function price(value: number): string {
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 3, maximumFractionDigits: 3 })
}

function timestamp(value?: string): string {
  return value?.trim().replace('T', ' ') || '未知'
}

function isOlderObservation(checkedAt?: string, lastCheck?: string): boolean {
  if (!checkedAt || !lastCheck) return false
  const checkedTime = Date.parse(checkedAt)
  const latestTime = Date.parse(lastCheck)
  return Number.isFinite(checkedTime) && Number.isFinite(latestTime) && checkedTime < latestTime
}

function accountStatusLabel(row: PaperCompareRow): string {
  if (row.status === 'frozen') return '账户已冻结'
  if (row.status !== 'active') return '账户状态未知'
  if (row.auto_enabled === false) return '策略已暂停'
  if (row.auto_enabled === true) return '策略已启用'
  return '启用状态未知'
}

function diagnosticValues(candidate: ShortTermCandidate): Array<{ label: string; value: string }> {
  const diagnostic = candidate.diagnostic
  if (!diagnostic) return []
  const values: Array<{ label: string; value: string }> = []
  const addPrice = (label: string, value: unknown) => {
    if (isFiniteNumber(value)) values.push({ label, value: '¥' + price(value) })
  }
  const addRatio = (label: string, value: unknown) => {
    if (isFiniteNumber(value)) values.push({ label, value: ratio(value) })
  }
  const addMoney = (label: string, value: unknown) => {
    if (isFiniteNumber(value)) values.push({
      label,
      value: '¥' + value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
    })
  }
  const addCount = (label: string, value: unknown) => {
    if (isFiniteNumber(value)) values.push({ label, value: value.toLocaleString('zh-CN') })
  }

  addPrice('观测参考价', diagnostic.quote_price)
  addPrice('近 30 分钟量均价', diagnostic.mean_30m)
  addRatio('近 30 分钟振幅', diagnostic.range_ratio)
  addRatio('回归空间', diagnostic.reversion_ratio)
  addRatio('价格偏离', diagnostic.spread_ratio)
  addRatio('费用门槛', diagnostic.required_ratio)
  addMoney('预计双边佣金', diagnostic.roundtrip_fee)
  addRatio('滑点率', diagnostic.slippage_ratio)
  addRatio('缓冲率', diagnostic.buffer_ratio)
  addCount('候选数量', diagnostic.qty)
  addCount('可卖数量', diagnostic.available_qty)
  addCount('持有数量', diagnostic.held_qty)
  if (diagnostic.bar_time?.trim()) values.push({ label: '分钟数据时间', value: timestamp(diagnostic.bar_time) })
  return values
}

/** Read-only rendering of the latest short-term ETF rule observations. */
export function PaperSignalDiagnostics({ row }: { row: PaperCompareRow }) {
  const shortterm = row.shortterm
  const symbols = shortterm?.symbols ?? {}
  const names = usePaperInstrumentNames(Object.keys(symbols))
  const [expanded, setExpanded] = useState(false)
  const candidates = useMemo(
    () => Object.entries(symbols)
      .map(([symbol, candidate]) => ({ symbol, candidate }))
      .sort((a, b) => a.symbol.localeCompare(b.symbol)),
    [symbols],
  )
  const status = shortterm?.status?.trim() || '状态未知'
  const stateError = (shortterm as (ShortTermState & { state_error?: unknown }) | null | undefined)?.state_error
  const hasStateError = Boolean(stateError)
  const errorDetail = typeof stateError === 'string' ? stateError : '短线运行状态读取失败'
  const needsRecentObservationNote = hasStateError || row.status === 'frozen' || row.auto_enabled === false ||
    /交易时段|暂停|冻结/.test(status)
  const categoryCounts = REASON_CATEGORIES
    .map(category => ({
      category,
      count: candidates.filter(({ candidate }) => classifyReason(candidate.reason) === category).length,
    }))
    .filter(item => item.count > 0)
  const visibleCandidates = expanded ? candidates : candidates.slice(0, 6)
  const hiddenCount = Math.max(0, candidates.length - 6)

  return <section className="rounded-card border border-border bg-base/40 p-3 text-xs text-secondary" aria-label={row.name + '短线观察诊断'}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        <h3 className="font-medium text-foreground">短线观察诊断</h3>
        <p className="mt-1 text-[11px] text-muted">账户：{accountStatusLabel(row)} · 运行状态：{status}</p>
      </div>
      <p className="text-[11px] text-muted">检查时间：{timestamp(shortterm?.last_check)}</p>
    </div>

    {needsRecentObservationNote && <p className="mt-3 rounded-lg border border-warning/30 bg-warning/10 p-2.5 leading-5 text-warning">
      以下候选理由来自最近一次观测；当前暂停、冻结、等待交易时段或状态异常时，它不是当前信号。
    </p>}
    {hasStateError && <p role="alert" className="mt-3 rounded-lg border border-danger/30 bg-danger/10 p-2.5 leading-5 text-danger">
      短线运行状态异常，不能确认当前检查结果：{errorDetail}
    </p>}

    {categoryCounts.length > 0 && <div className="mt-3" aria-label="原因分类汇总">
      <div className="mb-1.5 text-[11px] text-muted">原因分类</div>
      <ul className="flex flex-wrap gap-1.5">
        {categoryCounts.map(({ category, count }) => <li key={category} className="rounded-full border border-border bg-surface px-2 py-0.5 text-[11px]">
          {category} {count}
        </li>)}
      </ul>
    </div>}

    {candidates.length === 0
      ? <p className="mt-4 rounded-lg border border-border bg-surface/60 p-4 text-center text-muted">暂无短线候选观测</p>
      : <div className="mt-3 space-y-2">
        {visibleCandidates.map(({ symbol, candidate }) => {
          const reason = candidate.reason?.trim() || '未提供原因'
          const category = classifyReason(candidate.reason)
          const oldObservation = isOlderObservation(candidate.checked_at, shortterm?.last_check)
          const values = diagnosticValues(candidate)
          const action = candidate.action?.trim()
          const actionLabel = action ? ACTION_LABELS[action] ?? action : null
          const cycle = candidate.cycle
          return <article key={symbol} className="rounded-lg border border-border bg-surface/60 p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <PaperInstrumentLabel symbol={symbol} name={names.data?.names?.[symbol]} pending={names.isPending} />
              </div>
              <span className="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted">{category}</span>
            </div>

            <div className="mt-2 text-[11px] text-muted">
              <span>{candidate.checked_at?.trim() ? '观测时间：' + timestamp(candidate.checked_at) : '观测时间未知，不能确认当前信号'}</span>
              {oldObservation && <span className="ml-2 rounded bg-warning/10 px-1.5 py-0.5 text-warning">旧观测 · 不是当前信号</span>}
            </div>
            <p className="mt-2 break-words leading-5 text-foreground"><span className="text-muted">原因：</span>{reason}</p>

            {(actionLabel || cycle) && <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
              {actionLabel && <span>动作：{actionLabel}</span>}
              {cycle && <span>待回补周期：{isFiniteNumber(cycle.qty) ? cycle.qty.toLocaleString('zh-CN') + ' 份' : '数量未知'}{isFiniteNumber(cycle.price) ? ' · 卖出参考价 ¥' + price(cycle.price) : ''}</span>}
            </div>}

            {values.length > 0 && <details className="mt-2">
              <summary className="cursor-pointer text-[11px] text-muted">查看量价与费用明细</summary>
              <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
              {values.map(({ label, value }) => <div key={label} className="min-w-0 rounded-md border border-border/70 bg-base/50 px-2 py-1.5">
                <dt className="text-[10px] text-muted">{label}</dt>
                <dd className="mt-0.5 break-words font-mono tabular-nums text-foreground">{value}</dd>
              </div>)}
              </dl>
            </details>}
          </article>
        })}
        {hiddenCount > 0 && <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded(value => !value)}
          className="rounded-btn border border-border bg-surface px-3 py-2 text-[11px] text-secondary hover:text-foreground"
        >
          {expanded ? '收起候选' : '展开其余 ' + hiddenCount + ' 个候选'}
        </button>}
      </div>}
    <p className="mt-2 text-[10px] leading-5 text-muted">价差费用门槛含预计双边佣金、双边滑点和0.2%缓冲；实际建单还需满足原因中列出的条件，风控或尾盘退出可能不经过价差门槛。</p>
  </section>
}
