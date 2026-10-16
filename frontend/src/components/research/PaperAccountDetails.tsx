import { useEffect, useMemo, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import * as echarts from 'echarts'
import { api, type PaperCompareRow, type PaperNavItem } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { priceColorClass } from '@/lib/format'
import { finiteMoney, paperAccountMetrics } from '@/lib/paperAccountMetrics'
import { usePaperInstrumentNames } from '@/lib/usePaperInstrumentNames'
import { PaperInstrumentLabel } from './PaperInstrumentLabel'

const money = (value: unknown) => finiteMoney(value) ? value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
const percent = (value: unknown) => finiteMoney(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '—'
const price = (value: unknown) => finiteMoney(value) ? value.toFixed(3) : '—'

function Metric({ label, value, note, change }: { label: string; value: string; note?: string; change?: number | null }) {
  return <div className="min-w-0 rounded-lg border border-border bg-surface px-3 py-2.5">
    <div className="text-xs text-muted">{label}</div>
    <div title={value} className={`mt-1 truncate font-mono text-sm tabular-nums ${change == null ? 'text-foreground' : priceColorClass(change)}`}>{value}</div>
    {note && <div className="mt-1 text-[11px] text-muted">{note}</div>}
  </div>
}

function AccountCurve({ nav }: { nav: PaperNavItem[] }) {
  const ref = useRef<HTMLDivElement>(null)
  const metrics = useMemo(() => paperAccountMetrics(nav), [nav])
  useEffect(() => {
    if (!ref.current || !metrics.verifiedCount) return
    const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' })
    const render = () => {
      const dark = document.documentElement.classList.contains('dark')
      const text = dark ? '#8E8E96' : '#52525B'
      const line = dark ? '#353539' : '#E4E4E7'
      chart.setOption({
        backgroundColor: 'transparent', animation: false,
        legend: { top: 0, textStyle: { color: text }, itemWidth: 12, itemHeight: 3 },
        tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => finiteMoney(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '—' },
        grid: { left: 12, right: 16, top: 42, bottom: 42, containLabel: true },
        xAxis: { type: 'category', boundaryGap: false, data: metrics.series.map(p => p.date), axisLabel: { color: text }, axisLine: { lineStyle: { color: line } } },
        yAxis: [{ type: 'value', axisLabel: { color: text }, splitLine: { lineStyle: { color: line } } },
          { type: 'value', max: 0, axisLabel: { color: text, formatter: '{value}%' }, splitLine: { show: false } }],
        dataZoom: [{ type: 'inside' }],
        series: [
          { name: '总资产', type: 'line', symbol: 'none', connectNulls: false, data: metrics.series.map(p => p.equity), lineStyle: { width: 2, color: '#8B5CF6' }, itemStyle: { color: '#8B5CF6' } },
          { name: '现金', type: 'line', symbol: 'none', connectNulls: false, data: metrics.series.map(p => p.valid ? p.cash : null), lineStyle: { width: 1, color: '#94A3B8' }, itemStyle: { color: '#94A3B8' } },
          { name: '持仓市值', type: 'line', symbol: 'none', connectNulls: false, data: metrics.series.map(p => p.valid ? p.mv : null), lineStyle: { width: 1, color: '#10B981' }, itemStyle: { color: '#10B981' } },
          { name: '已观测回撤%', type: 'line', yAxisIndex: 1, symbol: 'none', connectNulls: false, data: metrics.series.map(p => p.drawdown), lineStyle: { width: 1, color: '#F43F5E' }, areaStyle: { color: '#F43F5E', opacity: .09 }, itemStyle: { color: '#F43F5E' } },
        ],
      })
    }
    render()
    const resize = new ResizeObserver(() => chart.resize())
    resize.observe(ref.current)
    const theme = new MutationObserver(render)
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => { resize.disconnect(); theme.disconnect(); chart.dispose() }
  }, [metrics])
  return <div className="rounded-lg border border-border bg-surface p-3">
    <div className="flex flex-wrap justify-between gap-2 text-xs"><span className="font-medium text-foreground">资产与回撤</span><span className="text-muted">盘后定版 · 完整记录 {metrics.verifiedCount}/{metrics.totalCount}</span></div>
    {metrics.verifiedCount ? <div ref={ref} className="mt-2 h-[260px] w-full" aria-label="账户总资产、现金、持仓市值与回撤曲线" /> : <div className="flex h-36 items-center justify-center text-xs text-muted">暂无完整定版净值，等待交易日盘后结算</div>}
    {metrics.verifiedCount < metrics.totalCount && <p className="text-[11px] text-muted">缺行情或旧版未标明质量的记录留空，不连接曲线；最大回撤待完整数据核验。</p>}
  </div>
}

/** Mounted by the account row only after expansion; all requests are read-only. */
export function PaperAccountDetails({ row }: { row: PaperCompareRow }) {
  const overview = useQuery({ queryKey: QK.paperOverview(row.account), queryFn: () => api.paperOverview(row.account), staleTime: 15_000, refetchInterval: 30_000 })
  const nav = useQuery({ queryKey: QK.paperNav(row.account), queryFn: () => api.paperNav(row.account), staleTime: 15_000, refetchInterval: 30_000 })
  const records = nav.data?.nav ?? []
  const metrics = useMemo(() => paperAccountMetrics(records), [records])
  const holdings = overview.data?.holdings ?? []
  const names = usePaperInstrumentNames(holdings.map(holding => holding.symbol))
  const empty = overview.isSuccess && overview.data.initialized && holdings.length === 0 && finiteMoney(overview.data.cash)
  const point = metrics.latest
  const equity = point?.nav ?? (empty ? overview.data?.cash : undefined)
  const cash = point?.cash ?? (overview.isSuccess ? overview.data?.cash : undefined)
  const mv = point?.mv ?? (empty ? 0 : undefined)
  const pnl = finiteMoney(equity) ? equity - row.initial_cash : null
  const pnlPct = pnl !== null && row.initial_cash > 0 ? pnl / row.initial_cash * 100 : null
  const dateLabel = point ? `${point.date} 定版${metrics.latestIsCurrent ? '' : '（最近完整日）'}` : empty ? '当前空仓账本 · 尚未定版' : '等待完整定版'
  const priced = overview.data?.valuation_complete === true
  return <section className="space-y-3" aria-label={`${row.name}账户详情`}>
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted"><span>{dateLabel}</span><span>检查时间 {row.shortterm?.last_check ?? '—'} · 初始本金 ¥{money(row.initial_cash)}</span></div>
    {row.shortterm?.state_error && <p role="alert" className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-xs text-danger">运行状态损坏，自动模拟已隔离：{row.shortterm.state_error}</p>}
    {(overview.isError || nav.isError) && <p role="alert" className="text-xs text-danger">账户详情读取失败，无法确认的数值暂不展示。</p>}
    <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
      <Metric label="总资产（元）" value={money(equity)} />
      <Metric label="现金（元）" value={money(cash)} note={!point ? '当前账本' : undefined} />
      <Metric label="持仓市值（元）" value={money(mv)} />
      <Metric label="累计盈亏（元）" value={money(pnl)} change={pnl} note={percent(pnlPct)} />
      <Metric label="已实现盈亏（元）" value={money(row.realized_pnl)} change={row.realized_pnl} />
      <Metric label="持仓标的" value={String(row.holdings_count ?? holdings.length)} note={`最多 ${row.max_positions ?? '—'} 只`} />
      <Metric label="已平仓回合" value={String(row.rounds)} />
      <Metric label="胜率" value={row.rounds > 0 && finiteMoney(row.win_rate) ? `${row.win_rate.toFixed(2)}%` : '—'} note={`${row.wins ?? '—'} / ${row.rounds} · 未平仓不计`} />
      <Metric label="盈亏比" value={row.rounds > 0 ? money(row.profit_loss_ratio) : '—'} note="平均盈利 / 平均亏损绝对值" />
      <Metric label="平均持有" value={row.rounds > 0 ? `${money(row.avg_holding_days)} 天` : '—'} />
      <Metric label="最大回撤" value={metrics.maxDrawdown === null ? '—' : `${metrics.maxDrawdown.toFixed(2)}%`} note="仅完整定版序列" />
      <Metric label="佣金 / 滑点" value={`${(row.fees.commission_pct * 10000).toFixed(1)}‱ / ${row.fees.slippage_bps}bps`} note={overview.data?.etf_shortterm ? 'ETF 每笔佣金最低 5 元' : '费用按账户设置'} />
    </div>
    {nav.isPending ? <div className="py-8 text-center text-xs text-muted">正在读取账户净值…</div> : <AccountCurve nav={records} />}
    <div className="rounded-lg border border-border bg-surface">
      <div className="flex flex-wrap justify-between gap-2 border-b border-border px-3 py-2 text-xs"><span className="font-medium text-foreground">当前持仓（{holdings.length}）</span><span className="text-muted">{priced ? overview.data?.valuation_note ?? '当前行情估值' : '现价及浮盈待完整行情核对'}</span></div>
      {holdings.length ? <div className="overflow-x-auto"><table className="w-full min-w-[610px] text-xs"><thead className="text-left text-muted"><tr>{['名称 / 代码', '份额 / 可卖', '成本', '现价', '市值', '浮动盈亏'].map(t => <th key={t} className="px-3 py-2 font-normal">{t}</th>)}</tr></thead><tbody>{holdings.map(h => <tr key={h.symbol} className="border-t border-border font-mono tabular-nums"><td className="px-3 py-2"><PaperInstrumentLabel symbol={h.symbol} name={names.data?.names[h.symbol]} pending={names.isPending} className="max-w-44" /></td><td className="px-3 py-2">{h.qty} / {h.available_qty}</td><td className="px-3 py-2">{price(h.avg_cost)}</td><td className="px-3 py-2">{priced ? price(h.last_price) : '—'}</td><td className="px-3 py-2">{priced ? money(h.market_value) : '—'}</td><td className={`px-3 py-2 ${priced ? priceColorClass(h.pnl) : 'text-muted'}`}>{priced ? `${money(h.pnl)} (${percent(h.pnl_pct)})` : '—'}</td></tr>)}</tbody></table></div> : <p className="p-5 text-center text-xs text-muted">{overview.isPending ? '正在读取持仓…' : overview.isError ? '持仓读取失败' : '暂无持仓，等待策略信号'}</p>}
    </div>
    <p className="text-[11px] text-muted">回撤和收益取盘后定版，持仓估值取当前行情，两者截止时间不同。历史实测不足时不显示年化收益或夏普比率。</p>
  </section>
}
