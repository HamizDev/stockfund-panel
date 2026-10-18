import { Link } from 'react-router-dom'

export function MarketBoardTabs({ market }: { market: 'cn' | 'hk' | 'us' }) {
  return <nav aria-label="看板市场切换" className="inline-flex rounded-btn border border-border bg-elevated/70 p-0.5">
    {([['cn', 'A股看板'], ['hk', '港股看板'], ['us', '美股看板']] as const).map(([id, label]) =>
      <Link key={id} to={`/?market=${id}`} aria-current={market === id ? 'page' : undefined}
        className={`rounded px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${market === id ? 'bg-accent/15 text-accent' : 'text-muted hover:bg-elevated hover:text-foreground'}`}>{label}</Link>,
    )}
  </nav>
}
