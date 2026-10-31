import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { cnDateFromUtc, cnDateTimeFromUtc, fmtPct, fmtPrice, priceColorClass } from '@/lib/format'
import { QK } from '@/lib/queryKeys'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import type {
  ResearchDecisionDetail,
  ResearchDecisionList,
} from '@/lib/researchTypes'

interface Props {
  recordId?: string | null
  compact?: boolean
}

const CARD = 'rounded-card border border-border bg-surface'
const MUTED = 'text-muted'

function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : '请求失败，请稍后重试。'
}

function numeric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function displayDate(value: string | null | undefined): string {
  return value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : '—'
}

function directionLabel(direction: string): string {
  if (direction === 'up') return '上涨'
  if (direction === 'down') return '下跌'
  return '未判断'
}

function statusLabel(status: string | null | undefined): string {
  switch (status) {
    case 'pending': return '待核对'
    case 'waiting_calendar': return '等待交易日历'
    case 'waiting_market': return '等待观察期收盘'
    case 'waiting_data': return '等待真实行情'
    case 'unscorable': return '不计方向成绩'
    case 'complete': return '已核对'
    default: return status || '状态未知'
  }
}

function rate(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : `${(value * 100).toFixed(2)}%`
}

function portfolioValue(value: unknown, options: { money?: boolean } = {}): string {
  const money = options.money ?? false
  if (value == null || value === '') return '—'
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '—'
    return money ? fmtPrice(value) : String(value)
  }
  if (typeof value === 'string') {
    if (!money) return value
    const parsed = Number(value)
    return Number.isFinite(parsed) ? fmtPrice(parsed) : value
  }
  return '—'
}

function asPortfolio(value: unknown): Record<string, unknown> | null {
  return value != null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function paperStateLabel(state: unknown): string {
  if (state === 'available') return '可用'
  if (state === 'unavailable') return '不可用'
  if (state === 'not_requested') return '未请求'
  if (state === 'held') return '有持仓'
  if (state === 'flat') return '空仓'
  return '未知'
}

function feeRate(value: unknown): string {
  const parsed = numeric(value)
  return parsed == null ? '—' : `${(parsed * 100).toFixed(4).replace(/\.?0+$/, '')}%`
}

function PortfolioSnapshot({ portfolio, assetType }: { portfolio: unknown; assetType?: string }) {
  const values = asPortfolio(portfolio)
  const broker = asPortfolio(values?.broker)
  const registered = asPortfolio(values?.registered_lots)
  const lots = Array.isArray(registered?.items) ? registered.items.map(asPortfolio).filter(Boolean) : []
  const accounts = Array.isArray(values?.paper_accounts)
    ? values.paper_accounts.map(asPortfolio).filter((account): account is Record<string, unknown> => !!account)
    : []

  return <details className="mt-3 rounded-btn border border-border/70 bg-base/30 px-3 py-2 text-xs">
    <summary className="cursor-pointer select-none font-medium text-secondary">纸面账户与实盘状态</summary>
    <div className="mt-2 space-y-2">
      <p className="text-muted">纸面账户上下文：{paperStateLabel(values?.paper_accounts_state)}</p>
      {lots.length > 0 && <section className="rounded-btn border border-border/70 p-2.5">
        <h4 className="font-medium text-secondary">用户登记的参考批次</h4>
        {lots.map((lot, index) => <p key={String(lot?.id ?? index)} className="mt-1 text-muted">
          登记数量 {portfolioValue(lot?.qty)} · 登记成本 {fmtPrice(numeric(lot?.cost_price), 3)}
        </p>)}
        <p className="mt-1 text-[10px] text-muted">未核对券商余额、可卖数量和费用；不与模拟账户合并。</p>
      </section>}
      {accounts.length ? accounts.map((account, index) => {
        const fees = asPortfolio(account.fees)
        return <section
          key={`${String(account.id ?? 'paper')}-${index}`}
          data-paper-account-id={String(account.id ?? '')}
          className="rounded-btn border border-border/70 bg-surface/70 p-2.5"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="font-medium text-foreground">纸面账户 {portfolioValue(account.id)}</h4>
            <span className="rounded-full border border-border/70 px-2 py-0.5 text-[10px] text-secondary">
              {paperStateLabel(account.state)}
            </span>
          </div>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
            <div><dt className={MUTED}>持仓数量</dt><dd className="mt-0.5 font-mono text-foreground">{portfolioValue(account.qty)}</dd></div>
            <div><dt className={MUTED}>可用数量</dt><dd className="mt-0.5 font-mono text-foreground">{portfolioValue(account.available_qty)}</dd></div>
            <div><dt className={MUTED}>平均成本</dt><dd className="mt-0.5 font-mono text-foreground">{fmtPrice(numeric(account.avg_cost), assetType === 'etf' ? 3 : 2)}</dd></div>
            <div><dt className={MUTED}>现金</dt><dd className="mt-0.5 font-mono text-foreground">{portfolioValue(account.cash, { money: true })}</dd></div>
            <div><dt className={MUTED}>佣金率</dt><dd className="mt-0.5 font-mono text-foreground">{feeRate(fees?.commission_pct)}</dd></div>
            <div><dt className={MUTED}>印花税率</dt><dd className="mt-0.5 font-mono text-foreground">{feeRate(fees?.stamp_tax_pct)}</dd></div>
            <div><dt className={MUTED}>滑点</dt><dd className="mt-0.5 font-mono text-foreground">{portfolioValue(fees?.slippage_bps)} 基点</dd></div>
            <div><dt className={MUTED}>最低佣金</dt><dd className="mt-0.5 font-mono text-foreground">{portfolioValue(fees?.min_commission, { money: true })}</dd></div>
          </dl>
          <p className="mt-2 text-[10px] text-muted">费用栏为纸面账户参数，不表示本次研究已发生费用。</p>
        </section>
      }) : <p className="text-secondary">没有可展示的纸面账户明细。</p>}
      <div className="rounded-btn border border-border/70 bg-base/40 p-2.5">
        <h4 className="font-medium text-secondary">实盘券商</h4>
        <p className="mt-1 text-muted">状态：{broker?.state === 'unknown' ? '未知（未连接）' : '未知'}</p>
        <p className="text-muted">实盘现金：未知 · 实盘可用数量：未知</p>
      </div>
      <p className="border-t border-border/60 pt-2 leading-relaxed text-muted">
        当前没有已核对的实盘上下文；这些内容仅供条件研究，不是下单许可。
      </p>
    </div>
  </details>
}

function ResultDetails({
  detail,
  isChecking,
  checkError,
  onCheck,
}: {
  detail: ResearchDecisionDetail
  isChecking: boolean
  checkError: unknown
  onCheck: () => void
}) {
  const { record, outcome } = detail
  const displayName = record.name?.trim() || record.symbol
  const questionDate = cnDateFromUtc(record.requested_at)
  const referencePrice = numeric(record.reference_price)
  const actualPrice = numeric(outcome?.actual_price)
  const priceChange = actualPrice != null && referencePrice != null
    ? actualPrice - referencePrice
    : null
  const percentChange = numeric(outcome?.price_return)
  const precision = record.asset_type === 'etf' ? 3 : 2
  const priceChangeLabel = priceChange == null
    ? '—'
    : `${priceChange > 0 ? '+' : ''}${fmtPrice(priceChange, precision)} (${fmtPct(percentChange)})`
  const outcomeMessage = typeof outcome?.message === 'string' && outcome.message.trim()
    ? outcome.message
    : null

  return <div className={`${CARD} p-4`} aria-label="研究复盘详情">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 className="text-sm font-semibold text-foreground">{displayName} · 研究复盘</h3>
        <p className="mt-1 text-[11px] text-muted">
          代码 {record.symbol} · 提问时间 {cnDateTimeFromUtc(record.requested_at) || record.requested_at || '—'}
          {' · '}记录时间 {cnDateTimeFromUtc(record.created_at) || record.created_at || '—'}
        </p>
      </div>
      <span className="rounded-full border border-accent/30 bg-accent/10 px-2 py-1 text-[10px] text-accent">
        {statusLabel(outcome?.status ?? 'pending')}
      </span>
    </div>

    <p className="mt-3 rounded-btn border border-border/60 bg-base/30 px-3 py-2 text-xs text-secondary">
      观察期限：{questionDate ? `提问日 ${questionDate} 之后第 ` : '提问日之后第 '}
      <span className="font-semibold text-foreground">{record.horizon_sessions}</span> 个实际交易日
      <span className="ml-2 text-muted">方向：{directionLabel(record.direction)}</span>
    </p>

    <dl className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-3">
      <div>
        <dt className={MUTED}>目标日期</dt>
        <dd className="mt-1 font-mono text-foreground">{displayDate(outcome?.target_date)}</dd>
      </div>
      <div>
        <dt className={MUTED}>原始参考价</dt>
        <dd className="mt-1 font-mono text-foreground">{fmtPrice(referencePrice, record.asset_type === 'etf' ? 3 : 2)}</dd>
      </div>
      <div>
        <dt className={MUTED}>目标原始收盘价</dt>
        <dd className="mt-1 font-mono text-foreground">{fmtPrice(actualPrice, record.asset_type === 'etf' ? 3 : 2)}</dd>
      </div>
      <div>
        <dt className={MUTED}>价格变化</dt>
        <dd className={`mt-1 font-mono ${priceColorClass(priceChange)}`}>{priceChangeLabel}</dd>
      </div>
      <div>
        <dt className={MUTED}>方向命中</dt>
        <dd className={`mt-1 ${outcome?.hit === true ? 'text-accent' : 'text-secondary'}`}>
          {outcome?.hit == null ? '未评定' : outcome.hit ? '命中' : '未命中'}
        </dd>
      </div>
      <div>
        <dt className={MUTED}>核对时间</dt>
        <dd className="mt-1 text-foreground">
          {outcome?.checked_at ? cnDateTimeFromUtc(outcome.checked_at) || outcome.checked_at : '—'}
        </dd>
      </div>
    </dl>

    <p className="mt-3 rounded-btn border border-border/60 bg-base/30 px-3 py-2 text-xs leading-relaxed text-secondary" role="status">
      {outcomeMessage || '系统在观察期后自动核对真实收盘行情；也可手动核对。缺少完整报价时不计分。'}
    </p>
    <p className="mt-2 text-[10px] leading-relaxed text-muted">
      这里只统计原始价格变化，不含分红、费用或成交约束，不是费用后交易收益。
    </p>
    {record.score_definition ? <p className="mt-1 text-[10px] leading-relaxed text-muted">成绩口径：{record.score_definition}</p> : null}

    {checkError ? <p role="alert" className="mt-2 text-xs text-amber-300">
      行情核对失败：{messageOf(checkError)}。现有复盘结果仍保留。
    </p> : null}
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={onCheck}
        disabled={isChecking}
        className="rounded-btn border border-accent/40 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent transition-colors hover:bg-accent/15 disabled:cursor-not-allowed disabled:opacity-50"
      >{isChecking ? '正在核对…' : '核对真实行情'}</button>
      <span className="text-[10px] text-muted">核对已保存的真实行情，不调用 AI。</span>
    </div>

    {record.content ? <details className="mt-3 rounded-btn border border-border/70 p-3">
      <summary className="cursor-pointer text-xs font-medium text-secondary">查看原研究结论</summary>
      <div className="mt-3 min-w-0 overflow-x-auto"><MarkdownRenderer content={record.content} /></div>
    </details> : null}
    <PortfolioSnapshot portfolio={record.portfolio} assetType={record.asset_type} />
  </div>
}

function DetailQueryState({
  pending,
  error,
  onRetry,
}: {
  pending: boolean
  error: unknown
  onRetry: () => void
}) {
  if (pending) return <p role="status" className={`${CARD} p-4 text-xs text-secondary`}>正在读取研究档案…</p>
  if (error) return <div role="alert" className={`${CARD} p-4 text-xs text-amber-300`}>
    研究档案读取失败：{messageOf(error)}
    <button type="button" onClick={onRetry} className="ml-2 text-accent underline">重试</button>
  </div>
  return null
}

export function ResearchReviewPanel({ recordId, compact = false }: Props) {
  const queryClient = useQueryClient()
  useEffect(() => {
    if (!recordId) return
    const cached = queryClient.getQueryData<ResearchDecisionList>(QK.researchDecisions)
    if (!cached?.items.some(item => item.id === recordId)) {
      void queryClient.invalidateQueries({ queryKey: QK.researchDecisions })
    }
  }, [queryClient, recordId])
  const [selection, setSelection] = useState<{ recordId: string | null; id: string | null }>({
    recordId: recordId?.trim() || null,
    id: null,
  })

  const listQuery = useQuery({
    queryKey: QK.researchDecisions,
    queryFn: ({ signal }) => api.researchDecisions(signal),
    enabled: !compact,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    retry: false,
    placeholderData: previous => previous,
  })
  const requestedId = recordId?.trim() || null
  const requestedSelection = selection.recordId === requestedId ? selection.id : null
  const requestedSelectionIsListed = !!requestedSelection
    && !!listQuery.data?.items.some(item => item.id === requestedSelection)
  const activeId = compact
    ? requestedId
    : requestedSelectionIsListed
      ? requestedSelection
      : requestedId ?? listQuery.data?.items[0]?.id ?? null

  const detailQuery = useQuery({
    queryKey: QK.researchDecision(activeId ?? ''),
    queryFn: ({ signal }) => api.researchDecision(activeId!, signal),
    enabled: !!activeId,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    refetchInterval: query => query.state.data?.outcome?.status === 'complete' ? false : 60_000,
    refetchIntervalInBackground: false,
    retry: false,
  })
  const refreshMutation = useMutation({
    mutationFn: (id: string) => api.researchDecisionRefresh(id),
    onSuccess: (outcome, id) => {
      queryClient.setQueryData<ResearchDecisionDetail>(
        QK.researchDecision(id),
        previous => previous ? { ...previous, outcome } : previous,
      )
      queryClient.setQueryData<ResearchDecisionList>(QK.researchDecisions, previous => {
        if (!previous) return previous
        return {
          ...previous,
          items: previous.items.map(item => item.id === id ? {
            ...item,
            status: outcome.status,
            target_date: outcome.target_date ?? item.target_date,
            price_return: outcome.price_return ?? item.price_return,
            hit: outcome.hit ?? item.hit,
          } : item),
        }
      })
      void queryClient.invalidateQueries({ queryKey: QK.researchDecisions })
    },
  })

  const refreshSelected = () => {
    if (activeId) refreshMutation.mutate(activeId)
  }
  const errorForCurrent = refreshMutation.variables === activeId ? refreshMutation.error : null

  if (compact) {
    if (!activeId) return <section aria-label="研究复盘" className={`${CARD} p-4`}>
      <h2 className="text-sm font-semibold text-foreground">AI 研究复盘</h2>
      <p className="mt-2 text-xs leading-relaxed text-secondary">
        这份历史报告未建档，因此没有复盘成绩；不会追补或改写历史记录。
      </p>
    </section>
    return <section aria-label="研究复盘" className="space-y-3">
      {detailQuery.data ? <ResultDetails
        detail={detailQuery.data}
        isChecking={refreshMutation.isPending && refreshMutation.variables === activeId}
        checkError={errorForCurrent}
        onCheck={refreshSelected}
      /> : null}
      {!detailQuery.data ? <DetailQueryState
        pending={detailQuery.isPending}
        error={detailQuery.error}
        onRetry={() => { void detailQuery.refetch() }}
      /> : null}
      {detailQuery.data && detailQuery.isError ? <p role="status" className="text-xs text-amber-300">
        档案更新失败，仍显示上次读取的结果。
      </p> : null}
    </section>
  }

  const list = listQuery.data
  return <section aria-label="AI 研究复盘" className="space-y-3">
    <div className={`${CARD} p-4`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-foreground">AI 研究复盘</h2>
          <p className="mt-1 text-[11px] text-muted">只读研究档案与事后方向核对；不代表策略或交易收益。</p>
        </div>
        <button
          type="button"
          onClick={() => { void listQuery.refetch() }}
          disabled={listQuery.isFetching}
          className="rounded-btn border border-border px-2.5 py-1 text-[11px] text-secondary hover:border-accent/40 hover:text-accent disabled:opacity-50"
        >{listQuery.isFetching ? '读取中…' : '刷新列表'}</button>
      </div>

      {listQuery.isPending && !list ? <p role="status" className="mt-3 text-xs text-secondary">正在读取最近研究记录…</p> : null}
      {listQuery.isError && !list ? <p role="alert" className="mt-3 text-xs text-amber-300">
        研究记录读取失败：{messageOf(listQuery.error)}
      </p> : null}
      {listQuery.isError && list ? <p role="status" className="mt-3 text-xs text-amber-300">
        列表刷新失败，仍显示上次读取的研究记录。
      </p> : null}

      {list ? <>
        <dl aria-label="研究复盘统计" className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="rounded-btn border border-border/70 bg-base/30 p-2">
            <dt className="text-[10px] text-muted">记录总数</dt>
            <dd className="mt-1 text-sm font-semibold text-foreground">{list.total}</dd>
          </div>
          <div className="rounded-btn border border-border/70 bg-base/30 p-2">
            <dt className="text-[10px] text-muted">方向命中率 · {list.statistics.evaluated} 条</dt>
            <dd className="mt-1 text-sm font-semibold text-foreground">{rate(list.statistics.direction_hit_rate)}</dd>
          </div>
          <div className="rounded-btn border border-border/70 bg-base/30 p-2">
            <dt className="text-[10px] text-muted">始终看涨基线</dt>
            <dd className="mt-1 text-sm font-semibold text-foreground">{rate(list.statistics.always_up_hit_rate)}</dd>
          </div>
          <div className="rounded-btn border border-border/70 bg-base/30 p-2">
            <dt className="text-[10px] text-muted">始终看跌基线</dt>
            <dd className="mt-1 text-sm font-semibold text-foreground">{rate(list.statistics.always_down_hit_rate)}</dd>
          </div>
        </dl>
        <p className="mt-2 text-[10px] leading-relaxed text-muted">
          显示最近 {list.recent_limit} 条。方向命中率只按已评定研究统计；始终看涨/看跌是价格方向基线。{list.limits}
        </p>
      </> : null}
    </div>

    {list?.items.length ? <div className={`${CARD} divide-y divide-border/60`} aria-label="最近研究记录">
      {list.items.map(item => {
        const displayName = item.name?.trim() || item.symbol
        return <article key={item.id} className={`px-3 py-2.5 ${item.id === activeId ? 'bg-accent/5' : ''}`}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <button
            type="button"
            aria-label={`查看 ${displayName} 研究复盘详情`}
            aria-pressed={item.id === activeId}
            onClick={() => {
              refreshMutation.reset()
              setSelection({ recordId: requestedId, id: item.id })
            }}
            className="text-left text-xs font-semibold text-accent hover:underline"
          >
            {displayName}
            {item.name?.trim() ? <span className="ml-1.5 font-mono text-[10px] font-normal text-secondary">{item.symbol}</span> : null}
          </button>
          <span className="text-[10px] text-muted">{cnDateTimeFromUtc(item.created_at) || item.created_at}</span>
          <span className="rounded-full border border-border/70 px-1.5 py-0.5 text-[9px] text-secondary">{directionLabel(item.direction)}</span>
          <span className="text-[10px] text-muted">{item.horizon_sessions} 个交易日 · {statusLabel(item.status)}</span>
          <span className="ml-auto font-mono text-[10px] text-secondary">目标 {displayDate(item.target_date)}</span>
          <span className={`font-mono text-[10px] ${priceColorClass(item.price_return)}`}>
            {item.price_return == null ? '—' : fmtPct(item.price_return)}
          </span>
          <span className="text-[10px] text-secondary">
            {item.hit == null ? '未评定' : item.hit ? '方向命中' : '方向未命中'}
          </span>
        </div>
      </article>
      })}
    </div> : list && list.items.length === 0 ? <p role="status" className={`${CARD} p-4 text-xs text-secondary`}>
      暂无已建档的研究复盘记录。旧报告不会自动建档或追补成绩。
    </p> : null}

    {list?.items.length ? activeId && detailQuery.data ? <ResultDetails
      detail={detailQuery.data}
      isChecking={refreshMutation.isPending && refreshMutation.variables === activeId}
      checkError={errorForCurrent}
      onCheck={refreshSelected}
    /> : activeId ? <DetailQueryState
      pending={detailQuery.isPending}
      error={detailQuery.error}
      onRetry={() => { void detailQuery.refetch() }}
    /> : <p role="status" className={`${CARD} p-4 text-xs text-muted`}>点击标的名称查看研究复盘详情。</p> : null}
    {list?.items.length && activeId && detailQuery.data && detailQuery.isError ? <p role="status" className="text-xs text-amber-300">
      档案更新失败，仍显示上次读取的结果。
    </p> : null}
  </section>
}
