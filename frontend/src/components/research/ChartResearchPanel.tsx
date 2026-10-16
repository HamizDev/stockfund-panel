import type { OHLC } from '@/components/EChartsCandlestick'
import { ResearchAIButton } from './ResearchAIButton'
import { chartPrice } from '@/lib/chartPrice'
import { chartResearchPrompt, researchMetrics, trendDescription } from '@/lib/chartResearch'

const pct = (value: number | null) => value == null ? '—' : `${(value * 100).toFixed(2)}%`
const num = (value: number | null) => value == null ? '—' : value.toFixed(2)

export function ChartResearchPanel({ symbol, data, selectedDate, onSelectDate, anchorDate, onAnchorDate, values, error }: {
  symbol: string; data: OHLC[]; selectedDate: string; onSelectDate: (value: string) => void
  anchorDate: string; onAnchorDate: (value: string) => void; values: Array<number | null>; error: string | null
}) {
  const selected = data.findIndex(row => row.date === selectedDate)
  const index = selected >= 0 ? selected : data.findLastIndex(row => !row.is_live)
  const row = data[index]
  const metrics = researchMetrics(row)
  const mean = values[index] ?? null
  const deviation = row && mean != null && mean > 0 ? row.close / mean - 1 : null
  return <section className="mb-2 rounded-card border border-border bg-base/40 p-3 text-xs" aria-label="日线研究指标">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-medium text-foreground">日线研究 · 趋势与波动</h3>
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-[11px] text-muted">观察日 <select aria-label="研究观察日" value={row?.date ?? ''} onChange={event => onSelectDate(event.target.value)} className="rounded border border-border bg-base px-1.5 py-1 text-secondary">
          {!row && <option value="">暂无完整日K</option>}
          {data.map(bar => <option key={bar.date} value={bar.date}>{bar.date}{bar.is_live ? ' · 未收盘' : ''}</option>)}
        </select></label>
        <ResearchAIButton label="AI 解读指标" disabled={!row} prompt={row ? chartResearchPrompt(symbol, row, metrics, anchorDate, mean) : ''} />
      </div>
    </div>
    {!row ? <p role="status" className="mt-2 text-muted">暂无已完成日K，请先加载日线。</p> : <>
      {row.is_live && <p className="mt-2 text-warning">该日尚未收盘，以下是变化中的观测值，不能视为已确认日线信号。</p>}
      <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="ADX(14) 趋势强度" value={num(metrics.adx)} detail={trendDescription(metrics)} title="Wilder SMA种子；至少28根连续有效日K才有首个ADX。20/25仅为研究参考，不是自动交易阈值。" />
        <Metric label="+DI / −DI 方向" value={`${num(metrics.plusDI)} / ${num(metrics.minusDI)}`} detail="ADX 高不等于上涨，方向看两条DI" />
        <Metric label="ATR 相对波动" value={pct(metrics.atrRatio)} detail="沿用看板 ATR(14) ÷ 收盘价" />
        <Metric label="布林带宽 / %B" value={`${pct(metrics.bollWidth)} / ${pct(metrics.bollPosition)}`} detail="%B：下轨0%，上轨100%；可超出两端" />
      </div>
    </>}
    <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border/60 pt-2">
      <label className="text-[11px] text-muted">锚定 VWAP 起点 <select aria-label="锚定VWAP起点" value={anchorDate} onChange={event => onAnchorDate(event.target.value)} className="rounded border border-border bg-base px-1.5 py-1 text-secondary">
        <option value="">关闭锚定线</option>
        {data.map(bar => <option key={bar.date} value={bar.date}>{bar.date}</option>)}
      </select></label>
      {anchorDate && <span className="font-mono text-amber-400">{row?.date ?? '—'} 均价 {mean == null ? '—' : chartPrice(mean)} · 收盘偏离 {pct(deviation)}</span>}
    </div>
    <p className="mt-1 text-[10px] leading-5 text-muted">使用已加载K线的前复权典型价×成交量估算，非真实主力成本；起点前不绘线。研究指标不改变模拟盘现有规则。</p>
    {error && <p role="status" className="text-[11px] text-warning">{error}</p>}
  </section>
}

function Metric({ label, value, detail, title }: { label: string; value: string; detail: string; title?: string }) {
  return <div className="min-w-0 rounded-btn border border-border/60 bg-surface p-2" title={title}>
    <div className="text-[10px] text-muted">{label}</div><div className="mt-1 font-mono text-sm text-foreground">{value}</div><p className="mt-1 text-[10px] leading-4 text-secondary">{detail}</p>
  </div>
}
