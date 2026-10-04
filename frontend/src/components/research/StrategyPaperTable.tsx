import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/cn'
import type { PaperCompareRow, StrategyDetail } from '@/lib/api'
import { priceColorClass } from '@/lib/format'

type PaperAssetType = 'stock' | 'etf'
type StrategyPaperRow = PaperCompareRow & {
  asset_type?: PaperAssetType | null
  wins?: number
  holdings_count?: number
  nav_date?: string | null
  settled_total?: number | null
  settled_pnl_pct?: number | null
  settled_max_drawdown?: number | null
  settled_nav_status?: 'complete' | 'missing_prices' | 'legacy_unknown' | 'unavailable'
}

type SortKey = 'pnl_pct' | 'max_drawdown' | 'win_rate'
type AssetFilter = 'all' | PaperAssetType

interface Props {
  rows: PaperCompareRow[]
  strategies: StrategyDetail[]
  onSelect: (accountId: string) => void
  onToggle: (accountId: string, enabled: boolean) => void
  togglePending: boolean
}

const FILTERS: Array<{ id: AssetFilter; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'stock', label: '股票' },
  { id: 'etf', label: 'ETF' },
]

const SORT_LABELS: Record<SortKey, string> = {
  pnl_pct: '累计收益',
  max_drawdown: '最大回撤',
  win_rate: '胜率',
}

function rowData(row: PaperCompareRow): StrategyPaperRow {
  return row as StrategyPaperRow
}

function finite(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function fmtMoney(value: number | null | undefined): string {
  if (!finite(value)) return '—'
  return `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function fmtPercent(value: number | null | undefined): string {
  if (!finite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function fmtWinRate(row: PaperCompareRow): string {
  if (!finite(row.rounds) || row.rounds < 0) return '未知'
  const rounds = row.rounds
  if (rounds === 0) return '— (0/0)'
  const wins = rowData(row).wins
  const winsLabel = finite(wins) && wins >= 0 ? String(wins) : '—'
  const rate = finite(row.win_rate) ? `${row.win_rate.toFixed(1)}%` : '—'
  return `${winsLabel}/${rounds} · ${rate}`
}

function getSortValue(row: PaperCompareRow, key: SortKey): number | null {
  if (key === 'win_rate' && (!finite(row.rounds) || row.rounds <= 0)) return null
  const value = key === 'pnl_pct'
    ? rowData(row).settled_pnl_pct
    : key === 'max_drawdown'
      ? rowData(row).settled_max_drawdown
      : row.win_rate
  return finite(value) ? value : null
}

function fmtFees(row: PaperCompareRow): string {
  const { commission_pct, stamp_tax_pct, slippage_bps } = row.fees
  return `${(commission_pct * 10000).toFixed(1)}‱ 佣金 · ${(stamp_tax_pct * 1000).toFixed(1)}‰ 印花税 · ${slippage_bps}bps 滑点`
}

function navDate(row: StrategyPaperRow): string {
  return row.nav_date ?? '—'
}

function settledStatusMessage(row: StrategyPaperRow): string | null {
  switch (row.settled_nav_status) {
    case 'complete':
      return null
    case 'missing_prices':
      return '行情缺失待核对'
    case 'legacy_unknown':
      return '历史净值待核对'
    case 'unavailable':
      return '待盘后定版'
    default:
      // 未知或旧客户端状态按质量未知处理，不能把历史值当成完整定版。
      return '历史净值待核对'
  }
}

function fmtSettledReturn(row: StrategyPaperRow): string {
  const statusMessage = settledStatusMessage(row)
  if (statusMessage) return statusMessage
  return finite(row.settled_pnl_pct) ? fmtPercent(row.settled_pnl_pct) : '—'
}

function fmtSettledTotal(row: StrategyPaperRow): string {
  const statusMessage = settledStatusMessage(row)
  if (statusMessage) return statusMessage
  return finite(row.settled_total) ? fmtMoney(row.settled_total) : '—'
}

function fmtClosedRounds(rounds: number | null | undefined): string {
  if (!finite(rounds) || rounds < 0) return '未知'
  return String(rounds)
}

function StrategyDetails({ row }: { row: PaperCompareRow }) {
  const data = rowData(row)
  const rounds = finite(row.rounds) && row.rounds >= 0 ? row.rounds : null
  const wins = finite(data.wins) && data.wins >= 0 ? String(data.wins) : '—'
  const holdings = finite(data.holdings_count) ? String(data.holdings_count) : '—'
  const roundSample = rounds == null
    ? '已平仓回合数未知，无法确认胜率样本量'
    : rounds > 0
      ? `${wins}/${rounds} 个 FIFO 配对的已平仓回合`
      : '暂无已平仓回合，样本为 0/0'

  return (
    <div className="grid gap-3 rounded-btn border border-border/60 bg-base/70 p-3 text-[11px] leading-5 text-secondary md:grid-cols-3">
      <div>
        <div className="font-medium text-foreground">胜率样本</div>
        <div>{roundSample}</div>
        <div className="text-muted">未平仓持仓不计入；零盈亏计为未胜。部分卖出按实际配对数量统计。</div>
      </div>
      <div>
        <div className="font-medium text-foreground">持仓与净值</div>
        <div>当前持仓标的 {holdings} 个 · 定版权益 {fmtSettledTotal(data)}</div>
        <div className="text-muted">只有行情完整的已保存收盘净值可用于累计收益；不完整或历史质量未知的净值数值会隐藏。净值日 {navDate(data)}。</div>
        <div className="text-muted">平均持有 {finite(row.avg_holding_days) ? `${row.avg_holding_days} 天` : '—'}（按已平仓回合的自然日）。</div>
      </div>
      <div>
        <div className="font-medium text-foreground">成本与已实现盈亏</div>
        <div>{fmtFees(row)}</div>
        <div className="text-muted">已实现盈亏 {fmtMoney(row.realized_pnl)}，按 FIFO 已平仓回合扣除买卖费用。</div>
        <div className="text-muted">历史模拟结果不代表未来收益。</div>
      </div>
    </div>
  )
}

export function StrategyPaperTable({ rows, strategies, onSelect, onToggle, togglePending }: Props) {
  const [assetFilter, setAssetFilter] = useState<AssetFilter>('all')
  const [sortKey, setSortKey] = useState<SortKey>('pnl_pct')
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc')
  const [expandedAccount, setExpandedAccount] = useState<string | null>(null)

  const strategyNames = useMemo(
    () => new Map(strategies.map(strategy => [strategy.id, strategy.name])),
    [strategies],
  )

  const visibleRows = useMemo(() => {
    const strategyRows = rows.filter(row => Boolean(row.strategy_id))
    const filtered = strategyRows.filter(row => (
      assetFilter === 'all' || rowData(row).asset_type === assetFilter
    ))

    return [...filtered].sort((a, b) => {
      const aValue = getSortValue(a, sortKey)
      const bValue = getSortValue(b, sortKey)
      if (aValue == null || bValue == null) {
        if (aValue == null && bValue == null) return a.account.localeCompare(b.account)
        return aValue == null ? 1 : -1
      }
      const difference = aValue - bValue
      if (difference !== 0) return sortDirection === 'asc' ? difference : -difference
      return a.account.localeCompare(b.account)
    })
  }, [assetFilter, rows, sortDirection, sortKey])

  const setSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDirection(direction => direction === 'asc' ? 'desc' : 'asc')
      return
    }
    setSortKey(key)
    setSortDirection(key === 'max_drawdown' ? 'asc' : 'desc')
  }

  const filters: Array<{ id: AssetFilter; label: string }> = FILTERS.map(filter => ({
    ...filter,
    label: filter.id === 'all' ? `${filter.label} (${rows.filter(row => Boolean(row.strategy_id)).length})` : filter.label,
  }))

  return (
    <section aria-label="策略模拟仓对比" className="min-w-0 rounded-card border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-foreground">策略模拟仓对比</h2>
          <p className="mt-1 text-[11px] leading-5 text-muted">
            虚拟资金 · 只有行情完整的已保存收盘净值可用于累计收益与权益；缺行情或历史质量未知时隐藏数值并待核对，无定版净值时显示“待盘后定版”。历史模拟结果不代表未来收益。
          </p>
        </div>
        <div className="flex flex-wrap gap-1 rounded-btn border border-border bg-base p-0.5" aria-label="按资产类型筛选">
          {filters.map(filter => (
            <button
              key={filter.id}
              type="button"
              aria-pressed={assetFilter === filter.id}
              aria-label={`筛选${filter.id === 'all' ? '全部' : filter.label}策略模拟仓`}
              onClick={() => setAssetFilter(filter.id)}
              className={cn(
                'rounded px-2.5 py-1 text-[11px] font-medium transition-colors',
                assetFilter === filter.id ? 'bg-accent/15 text-accent' : 'text-muted hover:text-foreground',
              )}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </div>

      <p className="mt-2 text-[11px] leading-5 text-muted">
        暂停只阻止新的策略信号订单，已经生成的待成交订单仍按原模拟规则处理。
      </p>

      <div className="mt-3 min-w-0 overflow-x-auto">
        {visibleRows.length === 0 ? (
          <div className="rounded-btn border border-dashed border-border p-6 text-center text-xs text-muted">
            {rows.some(row => row.strategy_id) ? '当前筛选下没有策略模拟仓。' : '暂无策略模拟仓。创建后可在此比较模拟结果。'}
          </div>
        ) : (
          <table className="w-full min-w-[1040px] table-auto text-xs">
            <thead>
              <tr className="border-b border-border text-left text-[11px] text-muted">
                <th scope="col" className="px-2 py-2 font-medium">策略 / 独立账户</th>
                <th scope="col" className="px-2 py-2 font-medium">资产</th>
                {(['pnl_pct', 'win_rate'] as const).map(key => (
                  <th
                    key={key}
                    scope="col"
                    aria-sort={sortKey === key ? (sortDirection === 'asc' ? 'ascending' : 'descending') : undefined}
                    className="px-2 py-2 text-right font-medium"
                  >
                    <button type="button" aria-label={`按${SORT_LABELS[key]}排序`} onClick={() => setSort(key)} className="inline-flex items-center justify-end gap-1 hover:text-foreground">
                      {key === 'pnl_pct' ? '累计收益' : SORT_LABELS[key]} <span aria-hidden="true">{sortKey === key ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}</span>
                    </button>
                  </th>
                ))}
                <th scope="col" className="px-2 py-2 text-right font-medium">已平仓回合</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">定版权益 / 净值日</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">持仓标的</th>
                <th
                  scope="col"
                  aria-sort={sortKey === 'max_drawdown' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : undefined}
                  className="px-2 py-2 text-right font-medium"
                >
                  <button type="button" aria-label="按最大回撤排序" onClick={() => setSort('max_drawdown')} className="inline-flex items-center justify-end gap-1 hover:text-foreground">
                    最大回撤 <span aria-hidden="true">{sortKey === 'max_drawdown' ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}</span>
                  </button>
                </th>
                <th scope="col" className="px-2 py-2 text-right font-medium">自动跟单</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">查看</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map(row => {
                const data = rowData(row)
                const strategyName = strategyNames.get(row.strategy_id ?? '') ?? row.name
                const rounds = finite(row.rounds) && row.rounds >= 0 ? row.rounds : null
                const isExpanded = expandedAccount === row.account
                const isEnabled = row.auto_enabled === true
                const autoStateKnown = typeof row.auto_enabled === 'boolean'
                const isFrozen = row.status === 'frozen'
                const canToggle = autoStateKnown && !togglePending && (isEnabled || !isFrozen)
                const accountLabel = `查看账户 ${strategyName}`

                return (
                  <FragmentRow
                    key={row.account}
                    row={row}
                    data={data}
                    strategyName={strategyName}
                    rounds={rounds}
                    isExpanded={isExpanded}
                    isEnabled={isEnabled}
                    autoStateKnown={autoStateKnown}
                    isFrozen={isFrozen}
                    canToggle={canToggle}
                    accountLabel={accountLabel}
                    onSelect={onSelect}
                    onToggle={onToggle}
                    onExpand={() => setExpandedAccount(current => current === row.account ? null : row.account)}
                  />
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}

function FragmentRow({
  row,
  data,
  strategyName,
  rounds,
  isExpanded,
  isEnabled,
  autoStateKnown,
  isFrozen,
  canToggle,
  accountLabel,
  onSelect,
  onToggle,
  onExpand,
}: {
  row: PaperCompareRow
  data: StrategyPaperRow
  strategyName: string
  rounds: number | null
  isExpanded: boolean
  isEnabled: boolean
  autoStateKnown: boolean
  isFrozen: boolean
  canToggle: boolean
  accountLabel: string
  onSelect: Props['onSelect']
  onToggle: Props['onToggle']
  onExpand: () => void
}) {
  const toggleLabel = !autoStateKnown
    ? `自动跟单状态未知 ${strategyName}`
    : `${isEnabled ? '暂停' : '启用'} ${strategyName} 自动跟单`
  const assetLabel = data.asset_type === 'stock' ? '股票' : data.asset_type === 'etf' ? 'ETF' : '—'
  const date = navDate(data)
  const holdings = finite(data.holdings_count) ? data.holdings_count : null
  const returnLabel = fmtSettledReturn(data)
  const totalLabel = fmtSettledTotal(data)
  const drawdown = data.settled_max_drawdown

  return (
    <>
      <tr className="border-b border-border/50 align-middle hover:bg-elevated/30">
        <td className="px-2 py-2">
          <div className="max-w-52 truncate font-medium text-foreground" title={strategyName}>{strategyName}</div>
          <div className="mt-0.5 truncate font-mono text-[10px] text-muted" title={`策略 ${row.strategy_id} · 账户 ${row.account}`}>
            {row.strategy_id} · {row.account}
          </div>
          {isFrozen && <span className="mt-1 inline-flex rounded bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning">已冻结</span>}
        </td>
        <td className="px-2 py-2 text-secondary">{assetLabel}</td>
        <td className={cn('px-2 py-2 text-right font-mono', data.settled_nav_status === 'complete' && finite(data.settled_pnl_pct) ? priceColorClass(data.settled_pnl_pct / 100) : 'text-muted')}>
          {returnLabel}
        </td>
        <td className="px-2 py-2 text-right font-mono text-foreground">{fmtWinRate(row)}</td>
        <td className="px-2 py-2 text-right font-mono text-secondary">{fmtClosedRounds(rounds)}</td>
        <td className="px-2 py-2 text-right">
          <div className="font-mono text-foreground">{totalLabel}</div>
          <div className="mt-0.5 text-[10px] text-muted">净值日 {date}</div>
        </td>
        <td className="px-2 py-2 text-right font-mono text-secondary">{holdings == null ? '—' : holdings}</td>
        <td className="px-2 py-2 text-right font-mono text-secondary">
          {finite(drawdown) ? `-${Math.abs(drawdown).toFixed(2)}%` : '—'}
        </td>
        <td className="px-2 py-2 text-right">
          <button
            type="button"
            aria-label={toggleLabel}
            title={isFrozen && !isEnabled ? '冻结账户不能启用自动跟单' : undefined}
            disabled={!canToggle}
            onClick={() => onToggle(row.account, !isEnabled)}
            className={cn(
              'whitespace-nowrap rounded-btn px-2 py-1 text-[10px] transition-colors disabled:cursor-not-allowed disabled:opacity-40',
              isEnabled ? 'bg-accent/10 text-accent hover:bg-accent/15' : 'bg-elevated text-muted hover:text-foreground',
            )}
          >
            {!autoStateKnown ? '状态未知' : isEnabled ? '已启用 · 暂停' : '已暂停 · 启用'}
          </button>
        </td>
        <td className="px-2 py-2 text-right">
          <div className="inline-flex items-center gap-1">
            <button
              type="button"
              aria-label={isExpanded ? `收起${strategyName}统计口径` : `展开${strategyName}统计口径`}
              aria-expanded={isExpanded}
              onClick={onExpand}
              className="rounded-btn border border-border px-2 py-1 text-[10px] text-secondary hover:border-accent/40 hover:text-accent"
            >
              {isExpanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              <span className="sr-only">{isExpanded ? '收起说明' : '统计口径'}</span>
            </button>
            <button
              type="button"
              aria-label={accountLabel}
              onClick={() => onSelect(row.account)}
              className="whitespace-nowrap rounded-btn border border-border px-2 py-1 text-[10px] text-accent hover:border-accent/40"
            >
              查看账户
            </button>
          </div>
        </td>
      </tr>
      {isExpanded && (
        <tr className="border-b border-border/50 bg-elevated/10">
          <td colSpan={10} className="p-2">
            <StrategyDetails row={row} />
          </td>
        </tr>
      )}
    </>
  )
}
