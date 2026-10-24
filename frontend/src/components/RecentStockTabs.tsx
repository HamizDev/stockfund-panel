import { Clock } from 'lucide-react'
import type { StockRef } from '@/lib/useLastStock'

/** Selection shortcuts only: inactive instruments never mount a chart or query. */
export function RecentStockTabs({ items, selected, onSelect, onClear }: {
  items: readonly StockRef[]
  selected: string
  onSelect: (symbol: string, name: string) => void
  onClear: () => void
}) {
  if (items.length === 0) return null
  return <nav aria-label="最近查看的标的" className="flex min-w-0 items-center gap-2 rounded-card border border-border/50 bg-surface/60 px-2.5 py-2">
    <span className="flex shrink-0 items-center gap-1 text-[10px] text-muted"><Clock className="h-3 w-3" aria-hidden="true" /><span className="hidden sm:inline">最近查看</span></span>
    <div className="flex min-w-0 flex-1 gap-1.5 overflow-x-auto">
      {items.map(item => <button
        key={item.symbol}
        type="button"
        aria-current={item.symbol === selected ? 'true' : undefined}
        title={`${item.name} · ${item.symbol}`}
        onClick={() => onSelect(item.symbol, item.name)}
        className={`min-h-8 max-w-40 shrink-0 truncate rounded-btn border px-2.5 py-1 text-xs transition-colors ${item.symbol === selected
          ? 'border-accent/40 bg-accent/10 text-accent'
          : 'border-border/60 bg-base/40 text-secondary hover:border-accent/30 hover:text-foreground'}`}
      >{item.name}</button>)}
    </div>
    <button type="button" onClick={onClear} aria-label="清除最近查看记录" title="仅清除本市场的名称记录，保留当前图表" className="min-h-8 shrink-0 rounded-btn px-1.5 text-[10px] text-muted hover:text-foreground">清除记录</button>
  </nav>
}
