import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { candidateHeatColor, candidateHeatmapStats, layoutCandidateHeatmap, type CandidateHeatmapItem, type HeatmapWeight } from '@/lib/candidateHeatmap'
import { formatMoney, formatPct, type Candidate } from './client'

export function CandidateHeatmapView({ candidates, selectedSymbol, weight, asOf, onWeightChange, onSelect }: {
  candidates: readonly { item: Candidate; rank: number }[]
  selectedSymbol: string | null
  weight: HeatmapWeight
  asOf: string | null
  onWeightChange: (weight: HeatmapWeight) => void
  onSelect: (symbol: string) => void
}) {
  const container = useRef<HTMLDivElement>(null)
  const descriptionId = useId()
  const [size, setSize] = useState({ width: 1000, height: 360 })
  const items = useMemo<CandidateHeatmapItem[]>(() => candidates.map(({ item, rank }) => ({
    symbol: item.symbol,
    name: item.name?.trim() || '名称暂缺',
    rank,
    changePct: item.metrics.change_pct,
    amount: item.metrics.amount,
    priceSource: item.price_source,
  })), [candidates])
  const stats = useMemo(() => candidateHeatmapStats(items), [items])
  const cells = useMemo(() => layoutCandidateHeatmap(items, size.width, size.height, weight), [items, size, weight])
  const drawn = new Set(cells.map(cell => cell.item.symbol))
  const small = cells.filter(cell => cell.width < 86 || cell.height < 60).map(cell => cell.item)
  const omitted = items.filter(item => !drawn.has(item.symbol))

  useEffect(() => {
    const element = container.current
    if (!element) return
    const measure = () => {
      const { width, height } = element.getBoundingClientRect()
      if (!(width > 0 && height > 0)) return
      setSize(previous => previous.width === width && previous.height === height ? previous : { width, height })
    }
    measure()
    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(measure)
      observer.observe(element)
      return () => observer.disconnect()
    }
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])

  const label = (item: CandidateHeatmapItem) => `${item.name}，排名 ${item.rank}，涨跌 ${formatPct(item.changePct)}，成交额 ${formatMoney(item.amount)}，${item.priceSource === 'live' ? '实时报价' : '盘后报价'}`
  const compactButton = (item: CandidateHeatmapItem) => <button
    key={item.symbol}
    type="button"
    aria-label={`查看${item.name}的候选详情`}
    aria-pressed={selectedSymbol === item.symbol}
    title={label(item) + ' · ' + item.symbol}
    onClick={() => onSelect(item.symbol)}
    className={`inline-flex min-h-9 min-w-0 items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${selectedSymbol === item.symbol ? 'border-accent bg-accent/10' : 'border-border bg-elevated/30 hover:border-accent/60'}`}
  ><span className="truncate text-foreground">{item.name}</span><span className="font-mono text-secondary">{formatPct(item.changePct)}</span></button>

  return <section aria-label="候选热力图" className="space-y-3 p-3 sm:p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs" aria-label="候选涨跌分布">
        <span className="text-secondary">当前 {stats.total} 只</span>
        <span className="text-bull">上涨 {stats.up}</span>
        <span className="text-bear">下跌 {stats.down}</span>
        <span className="text-muted">平盘 {stats.flat}</span>
        {stats.unknown > 0 && <span className="text-muted">涨跌暂缺 {stats.unknown}</span>}
      </div>
      <label className="flex shrink-0 items-center gap-2 text-[11px] text-muted">面积
        <select aria-label="热力图面积依据" value={weight} onChange={event => onWeightChange(event.target.value as HeatmapWeight)} className="rounded-md border border-border bg-base px-2 py-1.5 text-xs text-secondary">
          <option value="equal">等面积</option><option value="amount">成交额</option>
        </select>
      </label>
    </div>

    <div ref={container} role="group" aria-label="候选分布图" aria-describedby={descriptionId} className="relative h-[360px] w-full rounded-lg border border-border/40 bg-base/30">
      {cells.map(cell => {
        const smallCell = cell.width < 86 || cell.height < 60
        return <div key={cell.item.symbol} style={{ left: `${cell.x / size.width * 100}%`, top: `${cell.y / size.height * 100}%`, width: `${cell.width / size.width * 100}%`, height: `${cell.height / size.height * 100}%` }} className="absolute min-w-0 p-0.5">
        {smallCell ? <span aria-hidden="true" style={{ backgroundColor: candidateHeatColor(cell.item.changePct) }} className="block h-full rounded-md" /> : <button
        key={cell.item.symbol}
        type="button"
        aria-label={label(cell.item)}
        aria-pressed={selectedSymbol === cell.item.symbol}
        title={label(cell.item) + ' · ' + cell.item.symbol}
        onClick={() => onSelect(cell.item.symbol)}
        className="h-full w-full min-w-0 text-left focus-visible:relative focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      ><span style={{ backgroundColor: candidateHeatColor(cell.item.changePct) }} className={`flex h-full min-w-0 flex-col justify-center overflow-hidden rounded-md border px-2 text-white transition-colors hover:border-accent/60 ${selectedSymbol === cell.item.symbol ? 'border-accent ring-1 ring-inset ring-accent' : 'border-border/40'}`}>
        {cell.width >= 86 && cell.height >= 60 && <>
          <span className="truncate text-xs font-semibold">{cell.item.name}</span>
          <span className="mt-1 font-mono text-sm font-semibold">{formatPct(cell.item.changePct)}</span>
          {cell.height >= 84 && <span className="mt-1 truncate text-[10px] text-white/90">#{cell.item.rank}{weight === 'amount' ? ` · ${formatMoney(cell.item.amount)}` : ''}</span>}
        </>}
      </span></button>}</div>})}
      {!cells.length && <div role="status" className="flex h-full items-center justify-center px-4 text-center text-xs text-muted">{weight === 'amount' ? '暂无正成交额可用于面积布局，请切换等面积查看。' : '当前没有候选可展示。'}</div>}
    </div>
    {small.length > 0 && <div><p className="mb-2 text-[11px] text-muted">小面积候选（可点名称查看）</p><div className="flex flex-wrap gap-2">{small.map(compactButton)}</div></div>}
    {omitted.length > 0 && <div><p className="mb-2 text-[11px] text-muted">未绘制 {omitted.length} 只：成交额为零、暂缺或无效；仍可点名称查看。</p><div className="flex flex-wrap gap-2">{omitted.map(compactButton)}</div></div>}
    <p id={descriptionId} className="text-[11px] leading-5 text-muted">仅统计当前显示候选；实时报价 {stats.live} 只、盘后报价 {stats.daily} 只。策略日期 {asOf || '未知'}。颜色按各自报价时点的涨跌幅，红涨绿跌，±5% 色阶饱和；{weight === 'equal' ? '面积相同，便于比较。' : '面积按有效正成交额比例，不代表策略排名。'}点击色块查看原有研究详情。</p>
  </section>
}
