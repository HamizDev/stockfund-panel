import { Activity, BarChart3, Flame, Layers3, Radar, TrendingUp } from 'lucide-react'
import { dashboardPanel, KpiCell, SectionTitle } from '@/components/dashboard/Primitives'
import { boardMetrics, DISTRIBUTION_LABELS, rankedObservations } from './boardMetrics'
import { changeClass } from './overviewMetrics'
import { formatGlobalInstrumentPrice, formatGlobalMarketChange, formatGlobalMarketTime, GLOBAL_MARKET_CURRENCIES } from './marketFormatters'
import type { GlobalMarket, GlobalMarketCurrency, GlobalMarketOverviewItem, GlobalMarketSnapshot, GlobalMarketSnapshotSource, GlobalMarketSnapshotScope, GlobalSnapshotState, MarketSnapshotItem } from './types'

const pct = formatGlobalMarketChange
const ratio = (count: number, total: number) => total ? `${Math.round(count / total * 100)}%` : '—'
const large = (value: number | null) => value == null ? '—' : Math.abs(value) >= 1e8 ? `${(value / 1e8).toFixed(2)}亿` : Math.abs(value) >= 1e4 ? `${(value / 1e4).toFixed(1)}万` : value.toLocaleString('zh-CN')

function snapshotLabel(scope?: GlobalMarketSnapshotScope) {
  if (scope === 'hk_sina_hkd_pool' || scope === 'hk_sina_tencent_hkd_pool') return '港股港币覆盖池'
  if (scope === 'hk_etf_tencent_batch') return '港币ETF覆盖池'
  if (scope === 'hk_mainboard') return '港股主板股票'
  if (scope === 'us_screener_pool') return '美股活跃与涨跌筛选池（非全市场）'
  if (scope === 'us_listed_securities') return '美股上市证券（含基金、权证）'
  return '市场批量快照'
}

function snapshotSourceTiming(source: GlobalMarketSnapshotSource) {
  return source === 'sina_hk_public' || source === 'sina_hk_public+tencent_etf' || source === 'tencent_hk_etf' ? '时延未知' : '时延可能变化'
}

function snapshotScopeDescription(scope: GlobalMarketSnapshotScope) {
  if (scope === 'hk_sina_hkd_pool') return '仅统计已核验的港币证券报价，不代表全港市场，ETF覆盖也不完整。'
  if (scope === 'hk_sina_tencent_hkd_pool') return '股票与港币ETF合并，证券身份已经目录核验；仅统计已取得报价的覆盖池，不代表全港市场。'
  if (scope === 'hk_etf_tencent_batch') return '仅统计已核验的港币ETF报价，非全港ETF。'
  if (scope === 'us_screener_pool') return '仅合并活跃、涨幅、跌幅预设池，存在选择偏差，不能代表全市场。'
  if (scope === 'hk_mainboard') return '范围为港股主板股票。'
  return '范围为美股上市证券，包含基金、权证等。'
}

function missingRankDescription(source: GlobalMarketSnapshotSource, key: 'amount' | 'turnover' | 'volume' | 'market_cap') {
  if (key === 'amount' && source === 'yahoo_screener') return '当前覆盖池未提供成交额'
  if (key === 'turnover' && source === 'yahoo_screener') return '当前覆盖池未提供换手率'
  if (key === 'volume' && source === 'yahoo_screener') return '当前覆盖池未提供可用成交量'
  if (key === 'market_cap' && source === 'yahoo_screener') return '当前覆盖池未提供可用市值'
  if (key === 'turnover' && (source === 'sina_hk_public' || source === 'sina_hk_public+tencent_etf')) return '公开覆盖池未提供换手率'
  if (key === 'amount' && (source === 'sina_hk_public' || source === 'sina_hk_public+tencent_etf')) return '当前快照没有可核验的成交额'
  return '暂未取得有效排行'
}

type RankMetric = 'change' | 'amount' | 'turnover' | 'volume' | 'market_cap'
type RankCard = { key: RankMetric; title: string; rows: MarketSnapshotItem[] }

function rankValue(row: MarketSnapshotItem, key: RankMetric) {
  if (key === 'amount') return large(row.amount)
  if (key === 'turnover') return pct(row.turnover_rate)
  if (key === 'volume') return large(row.volume)
  if (key === 'market_cap') return large(row.market_cap ?? null)
  return pct(row.change_pct)
}

function rankUnit(row: MarketSnapshotItem, key: RankMetric, currency?: GlobalMarketCurrency) {
  if (key === 'amount' && row.source === 'sina_hk_public') return '原始值 · 单位待核'
  if (key === 'amount' || key === 'market_cap') return currency ?? '—'
  if (key === 'volume') return '股'
  return formatGlobalInstrumentPrice(row.price, currency ?? 'USD')
}

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
  const rankCards: RankCard[] = data?.scope === 'us_screener_pool'
    ? [
        { key: 'change', title: '涨幅榜', rows: data.ranks.gainers },
        { key: 'change', title: '跌幅榜', rows: data.ranks.losers },
        { key: 'volume', title: '成交量榜', rows: data.ranks.volume ?? [] },
        { key: 'market_cap', title: '总市值榜', rows: data.ranks.market_cap ?? [] },
      ]
    : [
        { key: 'change', title: '涨幅榜', rows: data?.ranks.gainers ?? [] },
        { key: 'change', title: '跌幅榜', rows: data?.ranks.losers ?? [] },
        { key: 'amount', title: '成交额榜', rows: data?.ranks.amount ?? [] },
        { key: 'turnover', title: '换手率榜', rows: data?.ranks.turnover ?? [] },
      ]
  if (state?.error && !data) return <section aria-label="批量市场快照" role="alert" className="mb-1.5 rounded-card border border-warning/30 bg-warning/5 px-3 py-2 text-[11px] text-warning">
    <p>公开市场批量快照暂不可用，未显示市场广度和全市场排行。</p>
    <p className="mt-1 text-muted">可继续查看产业篮子与核心观察池；数据源冷却后自动重试，不用空白卡片或零值代替缺失统计。</p>
  </section>
  return <section className="mb-1.5 space-y-1.5" aria-label="批量市场快照">
    <div className="flex flex-wrap items-center justify-between gap-1 rounded-card border border-border bg-surface px-2 py-1.5 text-[11px] text-muted">
      <span className="font-medium text-foreground">{snapshotLabel(data?.scope)} <span className="font-normal text-muted">· {data ? snapshotSourceTiming(data.source) : '时延可能变化'}</span></span>
      <span>{state?.loading ? data ? '正在更新 · 保留上一份完整快照' : '正在分页加载 · 不阻塞图表' : data ? `${data.date ?? '日期缺失'} · 有效 ${stats?.covered.toLocaleString()}/${data.total_reported.toLocaleString()}` : '等待快照'}</span>
    </div>
    {state?.error && <p role="alert" className="rounded border border-warning/30 bg-warning/5 px-2 py-1 text-[11px] text-warning">{state.stale ? '旧快照 · ' : ''}{state.error}</p>}
    <div className="grid grid-cols-2 gap-1.5 xl:grid-cols-6">
      <KpiCell label="上涨 / 平盘 / 下跌" value={stats ? <><span className="text-bull">{stats.up}</span><span className="text-muted">/{stats.flat}/</span><span className="text-bear">{stats.down}</span></> : '—'} sub="同一报价日 · 有效价格及涨跌幅" />
      <KpiCell label="大涨 / 大跌" value={stats ? `${stats.strong} / ${stats.weak}` : '—'} sub="涨跌达到 ±3% · 不是涨跌停" />
      <KpiCell label="平均涨跌" value={pct(stats?.mean)} sub="有效证券等权平均" tone={stats?.mean && stats.mean > 0 ? 'bull' : stats?.mean && stats.mean < 0 ? 'bear' : 'neutral'} />
      <KpiCell label="中位涨跌" value={pct(stats?.median)} sub="剔除缺失及异日记录" />
      <KpiCell label="上涨占比" value={stats ? ratio(stats.up, stats.covered) : '—'} sub={stats ? `覆盖 ${stats.covered.toLocaleString()} 只` : '等待市场快照'} tone="accent" />
      <KpiCell label="行业覆盖" value={data && data.industry_covered > 0 ? data.industry_covered.toLocaleString() : '—'} sub={data ? data.classification ? `行业目录 × 当前报价池 · 报价池匹配 ${data.classification.matched}/${data.total_reported} 只 · 目录 ${data.classification.row_count} 条` : data.industry_covered > 0 ? `${data.industries.length} 个行业 · 部分缺失` : (data.source === 'sina_hk_public' || data.source === 'sina_hk_public+tencent_etf') ? '公开覆盖池未提供行业分类' : data.source === 'yahoo_screener' ? '当前覆盖池未提供行业分类' : '暂无可用行业分类' : '等待行业分类'} />
    </div>
    <div className="grid gap-1.5 xl:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
      <section className={dashboardPanel}>
        <SectionTitle icon={BarChart3} title={data?.scope === 'us_screener_pool' ? '美股筛选池涨跌分布' : (data?.scope === 'hk_sina_hkd_pool' || data?.scope === 'hk_sina_tencent_hkd_pool') ? '港股覆盖池涨跌分布' : '涨跌分布 / 市场广度'} hint={data?.etf_coverage ? `港币ETF有效 ${data.etf_coverage.accepted}/${data.etf_coverage.requested} · ${data.complete ? '本轮已取得' : '部分报价缺失'}` : data?.complete ? data.scope === 'us_screener_pool' ? '三个预设池合并' : data.scope === 'hk_sina_hkd_pool' ? '覆盖池读取完成' : '分页完整' : '等待快照'} />
        {stats ? <Distribution bins={stats.bins} up={stats.up} flat={stats.flat} down={stats.down} /> : <p className="py-16 text-center text-xs text-muted">完整分页完成后显示统计</p>}
        <p className="mt-3 text-[10px] leading-4 text-muted">{data ? snapshotScopeDescription(data.scope) : '等待公开市场快照。'}缺失及异日报价不计入；源站未声明逐只交易状态时，无法独立核验停牌。来源池读取完成不代表覆盖了整个市场。</p>
      </section>
      <IndustryHeatmap data={data} onSelect={onSelect} />
    </div>
    <div className="grid gap-1.5 lg:grid-cols-2 xl:grid-cols-4">
      {rankCards.map((card, index) => <section key={card.title} className={dashboardPanel}>
        <SectionTitle icon={index < 2 ? TrendingUp : Activity} title={card.title} hint="TOP 8" />
        <div className="space-y-1">{card.rows.map((row, i) => <button key={row.symbol} type="button" onClick={() => onSelect(row)} className="flex min-h-11 w-full items-center gap-2 rounded-md border border-transparent bg-elevated/50 px-2 py-1.5 text-left hover:border-accent/40">
          <span className="w-3 shrink-0 font-mono text-[10px] text-muted">{i + 1}</span><span className="min-w-0 flex-1"><span className="block truncate text-xs text-foreground" title={row.name}>{row.name}</span><span className="block truncate font-mono text-[9px] text-muted">{row.symbol}</span></span>
          <span className="shrink-0 text-right font-mono text-[11px]"><span className={card.key === 'amount' || card.key === 'market_cap' ? 'text-foreground' : changeClass(card.key === 'turnover' ? row.turnover_rate : card.key === 'volume' ? null : row.change_pct)}>{rankValue(row, card.key)}</span><span className="mt-0.5 block text-[9px] text-muted">{rankUnit(row, card.key, data?.currency)}</span></span>
        </button>)}</div>
        {!card.rows.length && <p className="py-8 text-center text-xs text-muted">{data && (card.key === 'amount' || card.key === 'turnover' || card.key === 'volume' || card.key === 'market_cap') ? missingRankDescription(data.source, card.key) : '暂未取得有效排行'}</p>}
      </section>)}
    </div>
    {data?.ranks.amount.some(row => row.source === 'sina_hk_public') && <p className="px-1 text-[10px] text-muted">成交额单位尚未核实，展示原始值，不与其他口径直接比较；未核实单位的成交量留空。</p>}
    {data && <p className="min-w-0 break-words px-1 text-[10px] text-muted">报价截至 {data.quote_at_ms ? formatGlobalMarketTime(data.quote_at_ms / 1000, data.timezone, true) : '—'} · 本机采集 {formatGlobalMarketTime(data.fetched_at_ms / 1000, data.timezone, true)} · 缓存5分钟 · 未纳入 {data.skipped} 条</p>}
  </section>
}

function IndustryHeatmap({ data, onSelect }: { data?: GlobalMarketSnapshot | null; onSelect: (row: MarketSnapshotItem) => void }) {
  const groups = data?.industries ?? []
  const visible = [...groups.slice(0, 6), ...groups.slice(Math.max(6, groups.length - 6))]
  return <section className={dashboardPanel}>
    <SectionTitle icon={Layers3} title="行业热度 · 等权涨跌" hint="领涨 / 领跌 · 点击看代表标的" />
    <div className="grid grid-cols-2 gap-1.5 lg:grid-cols-3">
      {visible.map(group => <button key={group.name} type="button" onClick={() => onSelect(group.change_pct >= 0 ? group.leader : group.laggard)} className={`min-h-16 rounded-lg border px-2 py-2 text-left ${group.change_pct > 0 ? 'border-bull/20 bg-bull/10 hover:bg-bull/20' : group.change_pct < 0 ? 'border-bear/20 bg-bear/10 hover:bg-bear/20' : 'border-border bg-elevated'}`}>
        <span className="flex items-center justify-between gap-1"><span className="truncate text-xs font-medium text-foreground">{group.name}</span><span className={`shrink-0 font-mono text-xs font-semibold ${changeClass(group.change_pct)}`}>{pct(group.change_pct)}</span></span>
        <span className="mt-1 block truncate text-[10px] text-muted">{group.count}只 · {group.change_pct >= 0 ? group.leader.name : group.laggard.name}</span>
      </button>)}
    </div>
    {!visible.length && <p className="py-16 text-center text-xs text-muted">{(data?.source === 'sina_hk_public' || data?.source === 'sina_hk_public+tencent_etf') ? '公开覆盖池未提供行业分类，可查看上方产业代表篮子' : data?.source === 'yahoo_screener' ? '当前覆盖池未提供行业分类，可查看上方产业代表篮子' : '正在读取行业分类与市场快照'}</p>}
    {data?.classification && <div className="mt-2 space-y-1 text-[10px] leading-4 text-muted">
      <p>行业目录 × 当前报价池 · 报价池匹配 {data.classification.matched}/{data.total_reported} 只 · 目录 {data.classification.row_count} 条</p>
      <p>分类目录日期：{data.classification.source_as_of ?? '源站未给分类日期'}{data.classification.source_as_of ? ' · 不是行情报价日期' : ''}</p>
      {data.classification.error && <p role="status" className="text-warning">行业分类更新失败，暂用已有分类。</p>}
    </div>}
    <p className="mt-2 text-[10px] leading-4 text-muted">{data?.classification ? '行业分类只用于当前报价池分组；涨跌仍取各自行情源同日报价。' : '按源站行业内同日报价等权汇总，非官方行业指数；无分类者不参与，基金/权证可能缺分类。'}</p>
  </section>
}

export function CoreTechnicalPanels({ market, items, onSelect }: { market: GlobalMarket; items: GlobalMarketOverviewItem[]; onSelect: (symbol: string) => void }) {
  const metrics = boardMetrics(items, market === 'hk' ? 'Asia/Hong_Kong' : 'America/New_York')
  const axes = [metrics.above5, metrics.above20, metrics.above60, metrics.high, { count: metrics.up, total: metrics.covered }]
  const point = (i: number, radius: number) => `${100 + Math.sin(i * 2 * Math.PI / 5) * radius},${100 - Math.cos(i * 2 * Math.PI / 5) * radius}`
  return <div className="mb-1.5 grid gap-1.5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,2fr)]">
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
      <div className="grid grid-cols-2 gap-2">{(['momentum', 'volume'] as const).map((rank, i) => <div key={rank}><p className="mb-2 text-[11px] text-muted">{i ? '当日量 / 前5日均量' : '近20交易日涨幅'}</p>{rankedObservations(items, market === 'hk' ? 'Asia/Hong_Kong' : 'America/New_York', rank).slice(0, 5).map(row => <button key={row.symbol} type="button" onClick={() => onSelect(row.symbol)} className="mb-1 flex min-h-9 w-full items-center justify-between gap-1 rounded bg-elevated/50 px-2 text-left hover:bg-accent/10 sm:min-h-7"><span className="min-w-0 truncate text-[11px] text-foreground">{row.name}</span><span className={`shrink-0 font-mono text-[11px] ${i ? 'text-accent' : changeClass(row.daily?.return_20d)}`}>{i ? `${row.daily?.volume_ratio_5?.toFixed(2)}×` : pct(row.daily?.return_20d)}</span></button>)}</div>)}</div>
      {!metrics.technical.length && <p className="py-4 text-center text-xs text-muted">正在补齐核心观察池日K指标</p>}
      <p className="mt-2 text-[10px] text-muted">量能未按盘中经过时间调整，不等同实时量比；不代表买卖指令。币种 {GLOBAL_MARKET_CURRENCIES[market]}。</p>
    </section>
  </div>
}
