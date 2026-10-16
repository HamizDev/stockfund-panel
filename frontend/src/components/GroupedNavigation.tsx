import type { ReactNode } from 'react'
import { ChevronDown, ChevronRight, Database, Gauge, LayoutDashboard, RadioTower, ScanSearch, Sparkles, Wallet } from 'lucide-react'
import { cn } from '@/lib/cn'
import { groupNavigationItems, navigationGroupForPath, type NavigationGroupId } from '@/lib/navigationGroups'

const icons = { overview: LayoutDashboard, market: Gauge, ai: Sparkles, holdings: Wallet, strategy: ScanSearch, news: RadioTower, tools: Database }

export function GroupedNavigation<T extends { to: string }>({
  items, pathname, openGroup, onGroupChange, railMode, onExpandRail, renderItem,
}: {
  items: readonly T[]
  pathname: string
  openGroup: NavigationGroupId | null
  onGroupChange: (group: NavigationGroupId | null) => void
  railMode: boolean
  onExpandRail: () => void
  renderItem: (item: T) => ReactNode
}) {
  return groupNavigationItems(items).map(group => {
    const Icon = icons[group.id]
    const active = group.items.some(item => item.to === pathname) ||
      (pathname.startsWith('/analysis/') && navigationGroupForPath(pathname) === group.id)
    const expanded = !railMode && openGroup === group.id
    return (
      <div key={group.id}>
        <button
          type="button"
          title={railMode ? group.label : undefined}
          aria-label={group.label}
          aria-expanded={expanded}
          aria-controls={railMode ? undefined : `navigation-${group.id}`}
          onClick={() => {
            if (railMode) { onExpandRail(); onGroupChange(group.id) }
            else onGroupChange(expanded ? null : group.id)
          }}
          className={cn('group flex w-full items-center rounded-btn py-2 text-sm transition-colors hover:bg-elevated/70',
            railMode ? 'justify-center' : 'gap-3 px-3', active ? 'text-accent' : 'text-foreground/75 hover:text-foreground')}
        >
          <Icon className="h-4 w-4 shrink-0" />
          {!railMode && <>
            <span className="flex-1 text-left font-medium">{group.label}</span>
            {expanded ? <ChevronDown className="h-3.5 w-3.5 text-muted" /> : <ChevronRight className="h-3.5 w-3.5 text-muted" />}
          </>}
        </button>
        {!railMode && <div id={`navigation-${group.id}`} hidden={!expanded} className="ml-2 border-l border-border/60 pl-1">
          {expanded && group.items.map(item => <div key={item.to}>{renderItem(item)}</div>)}
        </div>}
      </div>
    )
  })
}
