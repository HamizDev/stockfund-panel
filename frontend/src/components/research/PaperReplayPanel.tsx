import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { History } from 'lucide-react'
import { api, type PaperReplayCase } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { priceColorClass } from '@/lib/format'

const money = (value: number) => value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const at = (value: string) => new Date(value).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })

export function PaperViewSwitch({ active, onChange }: {
  active: 'live' | 'replay'; onChange: (view: 'live' | 'replay') => void
}) {
  return <div className="flex shrink-0 rounded-btn border border-border bg-surface p-0.5" aria-label="模拟盘视图">
    {(['live', 'replay'] as const).map(view => <button key={view} type="button"
      aria-pressed={active === view} onClick={() => onChange(view)}
      className={cn('rounded px-2.5 py-1 text-xs transition-colors', active === view ? 'bg-accent text-white' : 'text-muted hover:text-foreground')}>
      {view === 'live' ? '自动模拟' : '历史重放'}
    </button>)}
  </div>
}

function EntryCard({ row, symbol }: { row: PaperReplayCase; symbol: string }) {
  return <article className="min-w-0 rounded-card border border-border bg-surface p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-base font-semibold text-foreground">¥{row.initial_cash.toLocaleString('zh-CN')} 建仓情景</h3>
      <span className="rounded bg-warning/10 px-2 py-0.5 text-[11px] text-warning">研究买入 · 未平仓</span>
    </div>
    <p className="mt-2 text-xs text-muted">原买点 {at(row.signal_at)} → 近似采样点 {at(row.sample_at)}</p>
    <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
      <div><div className="text-xs text-muted">标的 / 研究持仓</div><div className="mt-1 font-mono">{symbol} · {row.qty.toLocaleString('zh-CN')} 份</div></div>
      <div><div className="text-xs text-muted">模拟买价 / 买入佣金</div><div className="mt-1 font-mono">¥{row.assumed_fill_price.toFixed(4)} / ¥{money(row.buy_fee)}</div></div>
      <div><div className="text-xs text-muted">收盘浮盈 · 含买入费用</div><div className={cn('mt-1 font-mono text-lg', priceColorClass(row.close_floating_pnl))}>¥{money(row.close_floating_pnl)}</div></div>
      <div><div className="text-xs text-muted">剩余虚拟现金</div><div className="mt-1 font-mono">¥{money(row.remaining_cash)}</div></div>
    </div>
    <p className="mt-3 text-[11px] text-muted">当天可卖 {row.available_same_day} 份 · 股票ETF按T+1 · 卖出费用尚未发生</p>
  </article>
}

export function PaperReplayPanel() {
  const reportsQ = useQuery({ queryKey: QK.paperReplays, queryFn: api.paperReplays, staleTime: 60_000, refetchOnWindowFocus: false })
  const [selected, setSelected] = useState('')
  const [budget, setBudget] = useState('all')
  if (reportsQ.isLoading) return <div role="status" className="p-5 text-sm text-muted">读取历史重放…</div>
  if (reportsQ.isError) return <div role="alert" className="rounded-card border border-danger/30 bg-danger/5 p-4 text-sm">
    <p>历史重放暂时无法读取。</p><button type="button" onClick={() => reportsQ.refetch()} className="mt-2 text-accent">重新读取</button>
  </div>
  const reports = reportsQ.data?.replays ?? []
  const report = reports.find(item => item.id === selected) ?? reports[0]
  const errors = reportsQ.data?.errors ?? []
  if (!report) return <div className="rounded-card border border-border bg-surface p-5 text-sm text-muted" role="status">
    {errors.length ? '历史重放数据未通过校验，暂不展示交易结果。' : '暂无历史重放记录。导入独立回放结果后会在这里显示。'}
  </div>
  const budgets = [...new Set(report.alternatives.map(row => row.initial_cash))].sort((a, b) => a - b)
  const rows = report.alternatives.filter(row => budget === 'all' || row.initial_cash === Number(budget))
    .sort((a, b) => Date.parse(a.signal_at) - Date.parse(b.signal_at) || a.initial_cash - b.initial_cash)
  const buyPoints = new Set(report.alternatives.map(row => row.signal_at.slice(0, 16))).size
  return <section className="space-y-4" aria-label="历史分钟重放结果">
    <div className="rounded-card border border-warning/30 bg-warning/5 p-4">
      <div className="flex flex-wrap items-center gap-2"><History size={18} className="text-warning" />
        <h2 className="text-base font-semibold text-foreground">{report.title}</h2><span className="rounded bg-warning/15 px-2 py-0.5 text-xs text-warning">分钟级近似 · 只读</span>
      </div>
      <p className="mt-2 text-sm text-muted">独立历史研究情景，保留自动模拟账户的原始成交记录。逐笔报价与原90秒内的准确成交无法从分钟采样还原。</p>
      <p className="mt-2 text-xs text-muted">{report.source_name} · 收盘基准 {report.date} / ¥{report.raw_close.toFixed(4)} · 佣金万{report.commission_pct * 10_000}，最低¥{report.min_commission} · 买入滑点 {report.slippage_bps}bps</p>
    </div>
    {errors.length > 0 && <p role="alert" className="text-xs text-warning">另有一份回放数据未通过校验，已停止展示该记录。</p>}
    {reports.length > 1 && <label className="flex flex-wrap items-center gap-2 text-xs text-muted">回放记录
      <select aria-label="选择历史重放" className="max-w-full rounded-btn border border-border bg-surface p-2" value={report.id}
        onChange={event => { setSelected(event.target.value); setBudget('all') }}>
        {reports.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
      </select>
    </label>}
    <div><h3 className="mb-2 text-sm font-semibold">最早买点建仓后持有至收盘</h3>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">{report.primary.map(row => <EntryCard key={row.initial_cash} row={row} symbol={report.symbol} />)}</div>
    </div>
    <div className="rounded-card border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-4">
        <div><h3 className="text-sm font-semibold">逐点买入情景 · {buyPoints} 个买点 / {report.alternatives.length} 个情景</h3>
          <p className="mt-1 text-xs text-muted">每行独立使用原本金，不累计买入。</p></div>
        <label className="flex items-center gap-2 text-xs text-muted">本金
          <select aria-label="筛选回放本金" className="rounded-btn border border-border bg-surface px-2 py-1.5" value={budget} onChange={event => setBudget(event.target.value)}>
            <option value="all">全部本金</option>{budgets.map(value => <option key={value} value={value}>¥{value.toLocaleString('zh-CN')}</option>)}
          </select>
        </label>
      </div>
      <div className="max-w-full overflow-x-auto"><table className="w-full min-w-[960px] text-left text-xs">
        <thead className="text-muted"><tr>{['原买点', '近似采样点', '本金', '方向 / 标的', '数量', '模拟买价', '佣金', '收盘价', '收盘浮盈'].map(label => <th key={label} className="whitespace-nowrap px-3 py-3 font-medium">{label}</th>)}</tr></thead>
        <tbody>{rows.map(row => <tr key={`${row.initial_cash}-${row.signal_at}`} className="border-t border-border/60">
          <td className="px-3 py-3 font-mono">{at(row.signal_at)}</td><td className="px-3 py-3 font-mono">{at(row.sample_at)}</td>
          <td className="px-3 py-3 font-mono">¥{row.initial_cash.toLocaleString('zh-CN')}</td><td className="whitespace-nowrap px-3 py-3">研究买入 · {report.symbol}</td>
          <td className="px-3 py-3 font-mono">{row.qty.toLocaleString('zh-CN')}</td><td className="px-3 py-3 font-mono">{row.assumed_fill_price.toFixed(4)}</td>
          <td className="px-3 py-3 font-mono">{money(row.buy_fee)}</td><td className="px-3 py-3 font-mono">{row.close_price.toFixed(4)}</td>
          <td className={cn('px-3 py-3 font-mono', priceColorClass(row.close_floating_pnl))}>{money(row.close_floating_pnl)}</td>
        </tr>)}</tbody>
      </table></div>
    </div>
    <div className="rounded-card border border-border bg-surface p-4"><h3 className="text-sm font-semibold">回放口径</h3>
      <ul className="mt-2 space-y-1 text-xs leading-relaxed text-muted">{report.limitations.map((line, index) => <li key={index}>· {line}</li>)}</ul>
    </div>
  </section>
}
