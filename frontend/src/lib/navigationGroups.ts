export const NAVIGATION_GROUPS = [
  { id: 'overview', label: '总览', paths: ['/', '/indices'] },
  { id: 'market', label: '市场研究', paths: ['/market', '/concept-analysis', '/industry-analysis', '/regime', '/limit-ladder', '/stock-analysis', '/financials', '/markets/hk', '/markets/us'] },
  { id: 'ai', label: 'AI选品', paths: ['/ai-screener', '/ai-fund-screener'] },
  { id: 'holdings', label: '自选与持仓', paths: ['/watchlist', '/lots', '/fund'] },
  { id: 'strategy', label: '策略实验室', paths: ['/screener', '/backtest', '/paper', '/review', '/factors', '/signals', '/crypto-paper'] },
  { id: 'news', label: '资讯与提醒', paths: ['/news', '/monitor', '/abnormal'] },
  { id: 'tools', label: '数据工具', paths: ['/data'] },
] as const

export type NavigationGroupId = typeof NAVIGATION_GROUPS[number]['id']

export function navigationGroupForPath(pathname: string): NavigationGroupId {
  const path = pathname.split('?')[0]
  if (path.startsWith('/analysis/')) return 'market'
  return NAVIGATION_GROUPS.find(group => group.paths.some(route => route === path))?.id ?? 'tools'
}

/** Keep the user's visible items and ordering inside each group, including unknown extensions. */
export function groupNavigationItems<T extends { to: string }>(items: readonly T[]) {
  return NAVIGATION_GROUPS.map(group => ({
    ...group,
    items: items.filter(item => navigationGroupForPath(item.to) === group.id),
  })).filter(group => group.items.length > 0)
}
