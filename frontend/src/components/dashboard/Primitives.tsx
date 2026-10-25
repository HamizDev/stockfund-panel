import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

export const dashboardPanel = 'rounded-card border border-border bg-surface/80 p-1.5 shadow-[0_1px_2px_hsl(var(--border)/0.4)] backdrop-blur-sm'

export function SectionTitle({ icon: Icon, title, hint }: { icon: LucideIcon; title: string; hint?: ReactNode }) {
  return <div className="mb-2 flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
    <div className="flex min-w-0 items-center gap-1.5">
      <span className="h-3 w-0.5 shrink-0 rounded-full bg-gradient-to-b from-accent to-accent/30" />
      <Icon className="h-3.5 w-3.5 shrink-0 text-accent" />
      <h2 className="text-xs font-semibold text-foreground">{title}</h2>
    </div>
    {hint && <span className="min-w-0 max-w-full text-[10px] leading-relaxed text-muted break-words">{hint}</span>}
  </div>
}

export function KpiCell({ label, value, sub, tone = 'neutral' }: { label: ReactNode; value: ReactNode; sub?: string; tone?: 'bull' | 'bear' | 'accent' | 'neutral' }) {
  const isPlain = typeof value === 'string' || typeof value === 'number'
  const color = tone === 'bull' ? 'text-bull' : tone === 'bear' ? 'text-bear' : tone === 'accent' ? 'text-accent' : 'text-foreground'
  const valueClass = isPlain ? `${color} truncate` : 'text-foreground whitespace-normal break-words'
  return <div className="min-w-0 overflow-hidden rounded-lg border border-border bg-surface/80 px-2 py-1.5 shadow-[0_1px_2px_hsl(var(--border)/0.4)]">
    <div className="flex min-w-0 flex-wrap items-center gap-1 text-[11px] leading-snug text-muted">{label}</div>
    <div title={isPlain ? String(value) : undefined} className={`mt-1 font-mono text-lg font-semibold leading-tight tabular-nums ${valueClass}`}>{value}</div>
    {sub && <div title={sub} className="mt-1 text-[10px] leading-relaxed text-muted break-words">{sub}</div>}
  </div>
}
