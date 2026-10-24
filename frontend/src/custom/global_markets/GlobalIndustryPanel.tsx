import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Globe2, Info } from 'lucide-react'
import { dashboardPanel, SectionTitle } from '@/components/dashboard/Primitives'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { storage } from '@/lib/storage'
import { DASHBOARD_QUERY_GC_MS } from '@/lib/dashboardQueryCache'
import { progressiveLoadingInterval } from '@/lib/progressivePolling'
import { formatGlobalMarketChange } from './marketFormatters'
import { changeClass } from './overviewMetrics'
import type { IndustryMode } from './types'

function savedView() {
  const raw = storage.globalIndustryView.get(null)
  const view = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw as Record<string, unknown> : {}
  return { mode: ['auto', 'cn', 'us'].includes(String(view.mode)) ? view.mode as IndustryMode : 'auto' as IndustryMode,
    expanded: view.expanded === true, selected: typeof view.selected === 'string' ? view.selected : '' }
}

export function GlobalIndustryPanel() {
  const [view, setView] = useState(savedView)
  useEffect(() => storage.globalIndustryView.set(view), [view])
  const query = useQuery({ queryKey: QK.globalIndustries(view.mode),
    queryFn: ({ signal }) => api.globalIndustries(view.mode, signal), staleTime: 15_000, gcTime: DASHBOARD_QUERY_GC_MS,
    refetchInterval: query => {
      const data = query.state.data
      const progress = `${data?.date ?? ''}|${(data?.themes ?? []).map(theme => `${theme.id}:${theme.covered}/${theme.total}`).join(',')}`
      return progressiveLoadingInterval(query, data?.loading === true, progress, 30_000)
    }, refetchOnWindowFocus: false })
  const data = query.data?.mode === view.mode ? query.data : undefined
  const themes = data?.themes ?? []
  const preferred = ['ai_compute', 'cpo', 'pcb', 'semiconductor', 'memory', 'data_center', 'cloud', 'gold', 'petroleum', 'rare_earth']
  const visible = view.expanded ? themes : preferred.map(id => themes.find(theme => theme.id === id)).filter((theme): theme is typeof themes[number] => !!theme)
  const selected = themes.find(theme => theme.id === view.selected)
  return <section className={`${dashboardPanel} mb-1.5`} aria-label="全球产业数据" id="global-industries">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <SectionTitle icon={Globe2} title="全球产业数据" hint={<span className="text-accent">{data?.market === 'us' ? '美股代表篮子' : data?.market === 'cn' ? 'A股代表篮子' : '按时段切换'} · {data?.date ?? '等待报价'}</span>} />
      <div role="group" aria-label="全球产业市场" className="mb-2 flex rounded-btn border border-border bg-elevated p-0.5">
        {([['auto', '自动'], ['cn', 'A股'], ['us', '美股']] as const).map(([mode, name]) => <button type="button" key={mode} aria-pressed={view.mode === mode} onClick={() => setView(value => ({ ...value, mode }))} className={`min-h-7 rounded px-3 text-[11px] ${view.mode === mode ? 'bg-accent/15 text-accent' : 'text-muted hover:text-foreground'}`}>{name}</button>)}
      </div>
    </div>
    {(query.isPending || data?.loading) && <p role="status" className="mb-2 text-[11px] text-muted">{data?.themes.some(theme => theme.covered > 0) ? '正在更新，保留最近完整快照…' : '正在读取产业成分报价…'}</p>}
    {(query.isError || data?.error) && <p role="alert" className="mb-2 text-[11px] text-warning">{data?.stale ? '旧快照 · ' : ''}{data?.error ?? '产业数据读取失败，请稍后重试'}</p>}
    <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 xl:grid-cols-5">
      {visible.map(theme => <button type="button" key={theme.id} aria-expanded={view.selected === theme.id} aria-controls="global-industry-members" onClick={() => setView(value => ({ ...value, selected: value.selected === theme.id ? '' : theme.id }))} className={`min-h-12 rounded-lg border bg-elevated/45 px-2 py-2 text-left transition-colors hover:border-accent/40 ${view.selected === theme.id ? 'border-accent/50' : 'border-border'}`}>
        <span className="flex items-center justify-between gap-1"><span className="truncate text-xs font-medium text-foreground">{theme.name}</span><span className={`shrink-0 font-mono text-xs font-semibold ${changeClass(theme.change_pct)}`}>{formatGlobalMarketChange(theme.change_pct)}</span></span>
        <span className="mt-1 block text-[9px] text-muted">代表公司等权 · {theme.covered}/{theme.total}{theme.covered < theme.total ? ' · 报价不全' : ''}</span>
      </button>)}
    </div>
    {themes.length > 10 && <button type="button" onClick={() => setView(value => ({ ...value, expanded: !value.expanded }))} className="mx-auto mt-2 flex min-h-7 items-center gap-1 rounded px-3 text-[11px] text-accent hover:bg-accent/10">{view.expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}{view.expanded ? '收起产业列表' : `展开全部 ${themes.length} 个产业`}</button>}
    {selected && <div id="global-industry-members" className="mt-2 rounded-lg border border-accent/20 bg-elevated/30 p-2">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-1 text-xs"><span className="font-semibold text-foreground">{selected.name} · 成分与计算依据</span><span className={changeClass(selected.change_pct)}>{formatGlobalMarketChange(selected.change_pct)} · {selected.covered}/{selected.total}</span></div>
      <p className="mb-2 text-[11px] text-muted">{selected.note} 仅对当前报价日有效成分取算术平均，缺失、停牌旧日记录不计入；不同市场成分不同，不是跨市场连续收益。</p>
      <div className="space-y-1">{selected.members.map(member => <div key={member.symbol} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded bg-surface/70 px-2 py-1.5 text-[11px]">
        <span className="min-w-0 flex-1"><span className="font-medium text-foreground">{member.name}</span><span className="ml-2 font-mono text-[9px] text-muted">{member.symbol}</span></span>
        <span className="font-mono text-secondary">{member.price == null ? '—' : new Intl.NumberFormat('zh-CN', { style: 'currency', currency: data?.currency ?? 'USD', maximumFractionDigits: 3 }).format(member.price)}</span>
        <span className={`font-mono ${changeClass(member.change_pct)}`}>{formatGlobalMarketChange(member.change_pct)}</span><span className="text-[9px] text-muted">{member.quote_date ?? '报价缺失'}{member.basis === 'daily_reference' ? ' · 日线参考' : ''}{!member.included ? ' · 未计入' : ''}</span>
        {data?.market === 'us' && <Link to={`/?market=us&symbol=${encodeURIComponent(member.symbol)}`} className="text-accent">看K线</Link>}
      </div>)}</div>
    </div>}
    <p className="mt-2 flex items-start gap-1 text-[10px] leading-4 text-muted"><Info className="mt-0.5 h-3 w-3 shrink-0" /><span>自动按香港时间08:00—20:00看A股，其余看美股；可手动覆盖。代表股票篮子非官方产业指数，黄金/油气/金属为相关股票表现，非现货或期货报价。{data?.market === 'us' ? '仅保留报价日期，时区与延迟未确认，不代表实时行情或暗盘。' : '报价可能延迟，请留意各成分的数据日期。'}</span></p>
  </section>
}
