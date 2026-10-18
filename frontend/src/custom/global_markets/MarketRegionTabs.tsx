import { Link } from 'react-router-dom'
import type { GlobalMarket } from './types'

type ActiveMarket = 'cn' | GlobalMarket

const regions: { id: ActiveMarket; label: string; to: string }[] = [
  { id: 'cn', label: 'A股', to: '/stock-analysis' },
  { id: 'hk', label: '港股', to: '/markets/hk' },
  { id: 'us', label: '美股', to: '/markets/us' },
]

export function MarketRegionTabs({ activeMarket }: { activeMarket: ActiveMarket }) {
  return (
    <nav aria-label="市场研究市场切换" className="inline-flex flex-wrap items-center gap-0.5 rounded-btn border border-border bg-surface p-0.5">
      {regions.map(region => (
        <Link
          key={region.id}
          to={region.to}
          aria-current={activeMarket === region.id ? 'page' : undefined}
          className={`inline-flex min-h-7 items-center rounded-btn px-2 py-1 text-[11px] transition-colors sm:px-2.5 sm:text-xs ${
            activeMarket === region.id
              ? 'bg-accent/10 font-medium text-accent'
              : 'text-secondary hover:bg-elevated hover:text-foreground'
          }`}
        >
          {region.label}
        </Link>
      ))}
    </nav>
  )
}
