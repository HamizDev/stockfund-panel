import { Activity, BarChart3, Flame, Layers3, Radar, TrendingUp } from 'lucide-react'
import { dashboardPanel, KpiCell, SectionTitle } from '@/components/dashboard/Primitives'
import { boardMetrics, DISTRIBUTION_LABELS, rankedObservations } from './boardMetrics'
import { changeClass } from './overviewMetrics'
import { formatGlobalInstrumentPrice, formatGlobalMarketChange, formatGlobalMarketTime, GLOBAL_MARKET_CURRENCIES } from './marketFormatters'
import type { GlobalMarket, GlobalMarketOverviewItem, GlobalMarketSnapshot, GlobalSnapshotState, MarketSnapshotItem } from './types'

const pct = formatGlobalMarketChange
const ratio = (count: number, total: number) => total ? `${Math.round(count / total * 100)}%` : '—'
const large = (value: number | null) => value == null ? '—' : Math.abs(value) >= 1e8 ? `${(value / 1e8).toFixed(2)}亿` : Math.abs(value) >= 1e4 ? `${(value / 1e4).toFixed(1)}万` : value.toLocaleString('zh-CN')

export function Distribution({ bins, up, flat, down }: { bins: number[]; up: number; flat: number; down: number }) {
  const max = Math.max(1, ...bins)
  const total = up + flat + down
  return <>
    <div role="img" aria-label={`涨跌分布：${bins.join('、')}`} className="mt-3 flex h-32 items-end gap-1.5">
      {DISTRIBUTION_LABELS.map((label, i) => <div key={label} className="flex h-full min-w-0 flex-1 flex-col justify-end text-center">
        <span className="mb-1 font-mono text-[9px] text-secondary">{bins[i] ?? 0}</span>
        <div className={`mx-auto w-full max-w-8 rounded-t ${i < 4 ? 'bg-bear/85' : 'bg-bull/85'}`} style={{ height: `${(bins[i] ?? 0) / max * 85}px`, minHeight: bins[i] ? 2 : 0 }} />
        <span className="mt-1 whitespace-nowrap text-[8px] text-muted">{label}</span>
      </div>)}
    </div>
    <div className="mt-3 flex h-2 overflow-hidden rounded-full bg-elevated" aria-hidden="true">
      <span className="bg-bear" style={{ width: `${total ? down / total * 100 : 0}%` }} /><span className="bg-muted/60" style={{ width: `${total ? flat / total * 100 : 0}%` }} /><span className="bg-bull" style={{ width: `${total ? up / total * 100 : 0}%` }} />
    </div>
    <div className="mt-2 flex justify-between text-[11px]"><span className="text-bear">跌 {down}</span><span className="text-muted">平 {flat}</span><span className="text-bull">涨 {up}</span></div>
  </>
}

export function BroadMarketSummary({ state, onSelect }: { state?: GlobalSnapshotState; onSelect: (row: MarketSnapshotItem) => void }) {
  const data = state?.data
  const stats = data?.stats
  if (state?.error && !data) return <section aria-label="批量市场快照" role="alert" className="mb-1.5 rounded-card border border-warning/30 bg-warning/5 px-3 py-2 text-[11px] text-warning">
    <p>公开市场批量快照暂不可用，未显示市场广度和全市场排行。</p>
    <p className="mt-1 text-muted">可继续查看产业篮子与核心观察池；数据源冷却后自动重试，不用空白卡片或零值代替缺失统计。</p>
  </section>
  return <section className="mb-1.5 space-y-1.5" aria-label="批量市场快照">
    <div className="flex flex-wrap items-center justify-between gap-1 rounded-card border border-border bg-surface px-2 py-1.5 text-[11px] text-muted">
      <span className="font-medium text-foreground">{data?.universe_label ?? '市场批量快照'} <span className="font-normal text-muted">· {data?.source === 'yahoo_screener' ? 'Yahoo公开筛选池 · 可能延迟' : '东财公开源 · 约15分钟延迟'}</span></span>
      <span>{state?.loading ? data ? '正在更新 · 保留上一份完整快照' : '正在分页加载 · 不阻塞图表' : data ? `${data.date ?? '日期缺失'} · 有效 ${stats?.covered.toLocaleString()}/${data.total_reported.toLocaleString()}` : '等待快照'}</span>
    </div>
    {state?.error && <p role="alert" className="rounded border border-warning/30 bg-warning/5 px-2 py-1 text-[11px] text-warning">{state.stale ? '旧快照 · ' : ''}{state.error}</p>}
    {data?.warnings?.map(warning => <p key={warning} className="rounded border border-warning/30 bg-warning/5 px-2 py-1 text-[11px] text-warning">{warning}</p>)}
    <div className="grid grid-cols-2 gap-1.5 lg:grid-cols-6">
      <KpiCell label="上涨 / 平盘 / 下跌" value={stats ? <><span className="text-bull">{stats.up}</span><span className="text-muted">/{stats.flat}/</span><span className="text-bear">{stats.down}</span></> : '—'} sub="同一报价日 · 有效价格及涨跌幅" />
      <KpiCell label="大涨 / 大跌" value={stats ? `${stats.strong} / ${stats.weak}` : '—'} sub="涨跌达到 ±3% · 不是涨跌停" />
      <KpiCell label="平均涨跌" value={pct(stats?.mean)} sub="有效证券等权平均" tone={stats?.mean && stats.mean > 0 ? 'bull' : stats?.mean && stats.mean < 0 ? 'bear' : 'neutral'} />
      <KpiCell label="中位涨跌" value={pct(stats?.median)} sub="剔除缺失及异日记录" />
      <KpiCell label="上涨占比" value={stats ? ratio(stats.up, stats.covered) : '—'} sub={stats ? `覆盖 ${stats.covered.toLocaleString()} 只` : '等待市场快照'} tone="accent" />
      <KpiCell label="行业覆盖" value={data ? data.industry_covered.toLocaleString() : '—'} sub={data ? `${data.industries.length} 个源站行业 · 部分缺失` : '等待行业分类'} />
    </div>
    <div className="grid gap-1.5 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
      <section className={dashboardPanel}>
        <SectionTitle icon={BarChart3} title={data?.scope === 'us_screener_pool' ? '筛选池涨跌分布' : '涨跌分布 / 市场广度'} hint={data?.complete ? data.scope === 'us_screener_pool' ? '三个预设池合并' : '分页完整' : '等待完整快照'} />
        {stats ? <Distribution bins={stats.bins} up={stats.up} flat={stats.flat} down={stats.down} /> : <p className="py-16 text-center text-xs text-muted">完整分页完成后显示统计</p>}
        <p className="mt-3 text-[10px] leading-4 text-muted">{data?.scope === 'us_screener_pool' ? '仅活跃、涨幅、跌幅预设池，存在选择偏差，不能代表全市场。' : '港股为主板；美股包含基金、权证等。'}停牌、缺失、异日报价不计入，完整读取也不代表所有记录都有效。</p>
      </section>
      <IndustryHeatmap data={data} onSelect={onSelect} />
    </div>
    <div className="grid gap-1.5 md:grid-cols-2 xl:grid-cols-4">
      {(['gainers', 'losers', 'amount', 'turnover'] as const).map((key, index) => <section key={key} className={dashboardPanel}>
        <SectionTitle icon={index < 2 ? TrendingUp : Activity} title={['涨幅榜', '跌幅榜', '成交额榜', '换手率榜'][index]} hint="TOP 8" />
        <div className="space-y-1">{data?.ranks[key].map((row, i) => <button key={row.symbol} type="button" onClick={() => onSelect(row)} className="flex min-h-11 w-full items-center gap-2 rounded-md border border-transparent bg-elevated/50 px-2 py-1.5 text-left hover:border-accent/40">
          <span className="w-3 shrink-0 font-mono text-[10px] text-muted">{i + 1}</span><span className="min-w-0 flex-1"><span className="block truncate text-xs text-foreground" title={row.name}>{row.name}</span><span className="block truncate font-mono text-[9px] text-muted">{row.symbol}</span></span>
          <span className="shrink-0 text-right font-mono text-[11px]"><span className={key === 'amount' ? 'text-foreground' : changeClass(row.change_pct)}>{key === 'amount' ? large(row.amount) : key === 'turnover' ? pct(row.turnover_rate) : pct(row.change_pct)}</span><span className="mt-0.5 block text-[9px] text-muted">{key === 'amount' ? data.currency : formatGlobalInstrumentPrice(row.price, data.currency)}</span></span>
        </button>)}</div>
        {!data?.ranks[key].length && <p className="py-8 text-center text-xs text-muted">{data?.scope === 'us_screener_pool' && (key === 'amount' || key === 'turnover') ? '当前备用源不提供此字段' : '暂未取得有效排行'}</p>}
      </section>)}
    </div>
    {data && <p className="px-1 text-[10px] text-muted">源站报价截至 {data.quote_at_ms ? formatGlobalMarketTime(data.quote_at_ms / 1000, data.timezone, true) : '—'} · 本机采集 {formatGlobalMarketTime(data.fetched_at_ms / 1000, data.timezone, true)} · 缓存5分钟 · 无效代码/别名排除 {data.skipped} 条</p>}
  </section>
}

function IndustryHeatmap({ data, onSelect }: { data?: GlobalMarketSnapshot | null; onSelect: (row: MarketSnapshotItem) => void }) {
  const groups = data?.industries ?? []
  const visible = [...groups.slice(0, 6), ...groups.slice(Math.max(6, groups.length - 6))]
  return <section className={dashboardPanel}>
    <SectionTitle icon={Layers3} title="行业热度 · 等权涨跌" hint="领涨 / 领跌 · 点击看代表标的" />
    <div className="grid grid-cols-2 gap-1.5 md:grid-cols-3">
      {visible.map(group => <button key={group.name} type="button" onClick={() => onSelect(group.change_pct >= 0 ? group.leader : group.laggard)} className={`min-h-16 rounded-lg border px-2 py-2 text-left ${group.change_pct > 0 ? 'border-bull/20 bg-bull/10 hover:bg-bull/20' : group.change_pct < 0 ? 'border-bear/20 bg-bear/10 hover:bg-bear/20' : 'border-border bg-elevated'}`}>
        <span className="flex items-center justify-between gap-1"><span className="truncate text-xs font-medium text-foreground">{group.name}</span><span className={`shrink-0 font-mono text-xs font-semibold ${changeClass(group.change_pct)}`}>{pct(group.change_pct)}</span></span>
        <span className="mt-1 block truncate text-[10px] text-muted">{group.count}只 · {group.change_pct >= 0 ? group.leader.name : group.laggard.name}</span>
      </button>)}
    </div>
    {!visible.length && <p className="py-16 text-center text-xs text-muted">{data?.scope === 'us_screener_pool' ? '当前备用源无行业分类，可查看上方产业代表篮子' : '正在读取行业分类与市场快照'}</p>}
    <p className="mt-2 text-[10px] leading-4 text-muted">按源站行业内同日报价等权汇总，非官方行业指数；无分类者不参与，基金/权证可能缺分类。</p>
  </section>
}

export function CoreTechnicalPanels({ market, items, onSelect }: { market: GlobalMarket; items: GlobalMarketOverviewItem[]; onSelect: (symbol: string) => void }) {
  const metrics = boardMetrics(items, market === 'hk' ? 'Asia/Hong_Kong' : 'America/New_York')
  const axes = [metrics.above5, metrics.above20, metrics.above60, metrics.high, { count: metrics.up, total: metrics.covered }]
  const point = (i: number, radius: number) => `${100 + Math.sin(i * 2 * Math.PI / 5) * radius},${100 - Math.cos(i * 2 * Math.PI / 5) * radius}`
  return <div className="mb-1.5 grid gap-1.5 lg:grid-cols-[1fr_1fr_2fr]">
    <section className={dashboardPanel}><SectionTitle icon={Radar} title="结构雷达" hint="核心观察池 · 比例非评分" />
      <svg viewBox="0 0 200 200" role="img" aria-label="均线、新高、上涨覆盖比例" className="mx-auto h-44 w-full max-w-52 overflow-visible">
        {[.25, .5, .75, 1].map(scale => <polygon key={scale} points={axes.map((_, i) => point(i, 68 * scale)).join(' ')} fill="none" className="stroke-border" />)}
        <polygon points={axes.map((axis, i) => point(i, axis.total ? axis.count / axis.total * 68 : 0)).join(' ')} className="fill-accent/15 stroke-accent" strokeWidth="1.5" />
        {['MA5', 'MA20', 'MA60', '60日新高', '上涨'].map((label, i) => { const [x, y] = point(i, 88).split(','); return <text key={label} x={x} y={y} textAnchor="middle" className="fill-muted text-[9px]">{label}</text> })}
      </svg><p className="text-center text-[10px] text-muted">缺历史不计入对应分母 · {metrics.date ?? '等待日期'}</p>
    </section>
    <section className={dashboardPanel}><SectionTitle icon={TrendingUp} title="趋势强度" hint="核心观察池日K" />
      <div className="grid grid-cols-3 gap-1">{[metrics.above5, metrics.above20, metrics.above60].map((axis, i) => <KpiCell key={i} label={`站上MA${[5, 20, 60][i]}`} value={ratio(axis.count, axis.total)} sub={`${axis.count}/${axis.total}`} tone="accent" />)}</div>
      <div className="mt-1.5 grid grid-cols-2 gap-1"><KpiCell label="突破前60日高点" value={metrics.high.total ? metrics.high.count : '—'} sub={`有效 ${metrics.high.total}只`} tone="bull" /><KpiCell label="跌破前60日低点" value={metrics.low.total ? metrics.low.count : '—'} sub={`有效 ${metrics.low.total}只`} tone="bear" /></div>
      <p className="mt-2 text-[10px] leading-4 text-muted">仅固定核心观察池；均线用源站日K，不含分红总回报。末根日K可能未结束。</p>
    </section>
    <section className={dashboardPanel}><SectionTitle icon={Flame} title="动量与量能观察" hint="核心观察池 · 点击看K线" />
      <div className="grid grid-cols-2 gap-2">{(['momentum', 'volume'] as const).map((rank, i) => <div key={rank}><p className="mb-2 text-[11px] text-muted">{i ? '当日量 / 前5日均量' : '近20交易日涨幅'}</p>{rankedObservations(items, market === 'hk' ? 'Asia/Hong_Kong' : 'America/New_York', rank).slice(0, 5).map(row => <button key={row.symbol} type="button" onClick={() => onSelect(row.symbol)} className="mb-1 flex min-h-8 w-full items-center justify-between gap-1 rounded bg-elevated/50 px-2 text-left hover:bg-accent/10"><span className="min-w-0 truncate text-[11px] text-foreground">{row.name}</span><span className={`shrink-0 font-mono text-[11px] ${i ? 'text-accent' : changeClass(row.daily?.return_20d)}`}>{i ? `${row.daily?.volume_ratio_5?.toFixed(2)}×` : pct(row.daily?.return_20d)}</span></button>)}</div>)}</div>
      {!metrics.technical.length && <p className="py-4 text-center text-xs text-muted">正在补齐核心观察池日K指标</p>}
      <p className="mt-2 text-[10px] text-muted">量能未按盘中经过时间调整，不等同实时量比；不代表买卖指令。币种 {GLOBAL_MARKET_CURRENCIES[market]}。</p>
    </section>
  </div>
}
