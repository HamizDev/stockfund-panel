import { describe, expect, it } from 'vitest'
import {
  NAVIGATION_GROUPS,
  consolidateNavigationItems,
  groupNavigationItems,
  mergedNavigationIdForPath,
  navigationGroupForPath,
} from './navigationGroups'

describe('navigation groups', () => {
  it('defines the six navigation groups and classifies dynamic routes into tools', () => {
    expect(NAVIGATION_GROUPS.map(({ id, label }) => [id, label])).toEqual([
      ['overview', '总览行情'],
      ['market', '标的研究'],
      ['strategy', '选股策略'],
      ['holdings', '资产与模拟'],
      ['news', '资讯监控'],
      ['tools', '数据与工具'],
    ])

    expect(navigationGroupForPath('/markets/us?symbol=AAPL')).toBe('market')
    expect(navigationGroupForPath('/crypto-demo?account=sample')).toBe('holdings')
    expect(navigationGroupForPath('/extensions/custom-research?tab=overview')).toBe('tools')
    expect(navigationGroupForPath('/analysis/custom-menu?view=1')).toBe('tools')

    const grouped = groupNavigationItems([
      { to: '/', id: 'home' },
      { to: '/markets/hk?symbol=00700.HK', id: 'hk' },
      { to: '/research-tools?tab=library', id: 'research' },
      { to: '/extensions/custom-research', id: 'extension' },
      { to: '/analysis/custom-menu', id: 'analysis' },
    ])

    expect(grouped.map(group => group.id)).toEqual(['overview', 'market', 'tools'])
    expect(grouped.find(group => group.id === 'tools')?.items.map(item => item.id)).toEqual([
      'research',
      'extension',
      'analysis',
    ])
  })

  it('merges market and sector routes once at their first input positions without reordering other items', () => {
    const items = [
      { to: '/markets/hk', label: '港股', key: 'hk' },
      { to: '/research-tools?tab=library', label: '研究工具', key: 'research' },
      { to: '/industry-analysis', label: '行业', key: 'industry' },
      { to: '/paper', label: '模拟盘', key: 'paper' },
      { to: '/markets/us', label: '美股', key: 'us' },
      { to: '/concept-analysis', label: '概念', key: 'concept' },
      { to: '/stock-analysis', label: 'A股分析', key: 'stock' },
      { to: '/news', label: '快讯', key: 'news' },
    ]

    const result = consolidateNavigationItems(items, '/unrelated')
    expect(result.map(item => item.key)).toEqual(['stock', 'research', 'concept', 'paper', 'news'])
    expect(result.map(item => item.to)).toEqual([
      '/markets/hk',
      '/research-tools?tab=library',
      '/industry-analysis',
      '/paper',
      '/news',
    ])
    expect(result.map(item => item.label)).toEqual([
      '股票 / ETF 分析',
      '研究工具',
      '板块研究',
      '股票 / ETF 模拟',
      '快讯',
    ])
    expect(items.map(item => item.to)).toEqual([
      '/markets/hk',
      '/research-tools?tab=library',
      '/industry-analysis',
      '/paper',
      '/markets/us',
      '/concept-analysis',
      '/stock-analysis',
      '/news',
    ])
    expect(new Set(result.map(item => item.key)).size).toBe(result.length)
  })

  it('prefers the current visible route, then a preferred route, preserving query strings', () => {
    const items = [
      { to: '/markets/hk', label: '港股', key: 'hk' },
      { to: '/concept-analysis', label: '概念', key: 'concept' },
      { to: '/markets/us', label: '美股', key: 'us' },
      { to: '/industry-analysis', label: '行业', key: 'industry' },
      { to: '/stock-analysis', label: 'A股', key: 'stock' },
    ]

    expect(mergedNavigationIdForPath('/markets/us?symbol=AAPL#chart')).toBe('instrument')
    const result = consolidateNavigationItems(items, '/stock-analysis?symbol=600519.SH', {
      instrument: '/markets/hk?symbol=00700.HK',
      sector: '/industry-analysis?level=2',
    })

    expect(result.map(item => item.to)).toEqual([
      '/stock-analysis?symbol=600519.SH',
      '/industry-analysis?level=2',
    ])
  })

  it('does not restore hidden routes when current and preferred destinations are unavailable', () => {
    const visibleItems = [
      { to: '/markets/us?watchlist=1', label: '美股', key: 'us' },
      { to: '/industry-analysis?level=2', label: '行业', key: 'industry' },
      { to: '/research-tools?tab=library', label: '研究工具', key: 'research' },
    ]

    const result = consolidateNavigationItems(visibleItems, '/stock-analysis?symbol=600519.SH', {
      instrument: '/markets/hk?symbol=00700.HK',
      sector: '/concept-analysis?sort=heat',
    })

    expect(result.map(item => item.to)).toEqual([
      '/markets/us?watchlist=1',
      '/industry-analysis?level=2',
      '/research-tools?tab=library',
    ])
    expect(result.some(item => item.to.startsWith('/stock-analysis'))).toBe(false)
    expect(result.some(item => item.to.startsWith('/markets/hk'))).toBe(false)
    expect(result.some(item => item.to.startsWith('/concept-analysis'))).toBe(false)
    expect(new Set(result.map(item => item.key)).size).toBe(result.length)
  })
})