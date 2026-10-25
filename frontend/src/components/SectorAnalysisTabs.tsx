import { NavLink } from 'react-router-dom'

/** One sidebar entry, two existing datasets and independent page configurations. */
export function SectorAnalysisTabs() {
  return <nav aria-label="板块研究分类" className="inline-flex items-center gap-0.5 rounded-btn border border-border bg-surface p-0.5">
    {[['/concept-analysis', '概念'], ['/industry-analysis', '行业']].map(([to, label]) =>
      <NavLink key={to} to={to} className={({ isActive }) => `inline-flex min-h-9 items-center rounded-btn px-3 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${isActive ? 'bg-accent/10 font-medium text-accent' : 'text-secondary hover:bg-elevated hover:text-foreground'}`}>
        {label}
      </NavLink>)}
  </nav>
}
