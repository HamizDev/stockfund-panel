export const NAVIGATION_GROUPS = [
  { id: 'overview', label: '总览行情', paths: ['/', '/indices', '/regime', '/limit-ladder', '/market'] },
  { id: 'market', label: '标的研究', paths: ['/concept-analysis', '/industry-analysis', '/stock-analysis', '/financials', '/markets/hk', '/markets/us'] },
  { id: 'strategy', label: '选股策略', paths: ['/ai-screener', '/ai-fund-screener', '/screener', '/backtest', '/review', '/factors', '/signals'] },
  { id: 'holdings', label: '资产与模拟', paths: ['/watchlist', '/lots', '/fund', '/paper', '/crypto-paper', '/crypto-demo'] },
  { id: 'news', label: '资讯监控', paths: ['/news', '/monitor', '/abnormal'] },
  { id: 'tools', label: '数据与工具', paths: ['/data', '/research-tools', '/indicator-library'] },
] as const

export type NavigationGroupId = typeof NAVIGATION_GROUPS[number]['id']

const MERGED_ENTRIES = [
  { id: 'instrument', label: '股票 / ETF 分析', paths: ['/stock-analysis', '/markets/hk', '/markets/us'] },
  { id: 'sector', label: '板块研究', paths: ['/concept-analysis', '/industry-analysis'] },
] as const

export type MergedNavigationId = typeof MERGED_ENTRIES[number]['id']
export type MergedNavigationTargets = Partial<Record<MergedNavigationId, string>>

const LABELS: Record<string, string> = {
  '/market': '资金与人气', '/screener': '策略筛选', '/factors': '因子研究',
  '/backtest': '策略回测', '/review': '复盘记录', '/fund': '基金中心',
  '/paper': '股票 / ETF 模拟', '/crypto-paper': '数字资产本地模拟', '/data': '数据与同步',
}

function pathname(value: string) { return value.split(/[?#]/, 1)[0] }

export function mergedNavigationIdForPath(value: string): MergedNavigationId | undefined {
  const path = pathname(value)
  return MERGED_ENTRIES.find(entry => entry.paths.some(route => route === path))?.id
}

export function navigationGroupForPath(pathname: string): NavigationGroupId {
  const path = pathname.split(/[?#]/, 1)[0]
  return NAVIGATION_GROUPS.find(group => group.paths.some(route => route === path))?.id ?? 'tools'
}

/** Merge visible entries after existing order/visibility preferences apply. */
export function consolidateNavigationItems<T extends { to: string; label: string }>(
  items: readonly T[], current: string, preferred: MergedNavigationTargets = {},
): T[] {
  const result: T[] = []
  const seen = new Set<MergedNavigationId>()
  for (const item of items) {
    const id = mergedNavigationIdForPath(item.to)
    if (!id) {
      result.push(LABELS[pathname(item.to)] ? { ...item, label: LABELS[pathname(item.to)] } : item)
      continue
    }
    if (seen.has(id)) continue
    seen.add(id)
    const entry = MERGED_ENTRIES.find(value => value.id === id)!
    const visible = items.filter(value => mergedNavigationIdForPath(value.to) === id)
    const available = (value: string | undefined) => value && visible.some(row => pathname(row.to) === pathname(value))
    const destination = available(current) ? current : available(preferred[id]) ? preferred[id]! : visible[0].to
    const representative = visible.find(row => pathname(row.to) === entry.paths[0]) ?? visible[0]
    result.push({ ...representative, label: entry.label, to: destination })
  }
  return result
}

/** Keep the user's visible items and ordering inside each group, including unknown extensions. */
export function groupNavigationItems<T extends { to: string }>(items: readonly T[]) {
  return NAVIGATION_GROUPS.map(group => ({
    ...group,
    items: items.filter(item => navigationGroupForPath(item.to) === group.id),
  })).filter(group => group.items.length > 0)
}
