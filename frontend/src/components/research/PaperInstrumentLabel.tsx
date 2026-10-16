import { cn } from '@/lib/cn'

export function PaperInstrumentLabel({ symbol, name, pending = false, className }: {
  symbol: string
  name?: string
  pending?: boolean
  className?: string
}) {
  const label = name?.trim() || (pending ? '名称读取中…' : '名称暂缺')
  return <span className={cn('inline-flex min-w-0 flex-col align-middle font-sans', className)} title={`${label} · ${symbol}`}>
    <span className="truncate text-xs font-medium text-foreground">{label}</span>
    <span className="mt-0.5 font-mono text-[10px] text-muted">{symbol}</span>
  </span>
}
