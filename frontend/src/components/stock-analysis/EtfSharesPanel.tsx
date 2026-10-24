import { useQuery } from '@tanstack/react-query'
import { Database, RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { SectionTitle, dashboardPanel } from '@/components/dashboard/Primitives'
import { INSTRUMENT_VIEW_GC_MS } from '@/lib/useInstrumentViewCache'

export function EtfSharesPanel({ symbol, date = null }: { symbol: string; date?: string | null }) {
  const query = useQuery({
    queryKey: QK.etfShares(symbol, date),
    queryFn: ({ signal }) => api.etfShares(symbol, date, signal),
    staleTime: 1800_000, gcTime: INSTRUMENT_VIEW_GC_MS, retry: false, refetchOnWindowFocus: false,
    meta: { instrumentView: 'analysis', symbol },
  })
  const data = query.data?.symbol === symbol ? query.data : undefined
  const error = data?.error || (query.error instanceof Error ? query.error.message : null)
  return <section className={`${dashboardPanel} col-span-full`} aria-label="ETF官方份额">
    <SectionTitle icon={Database} title="ETF官方份额" hint={<span className="flex min-w-0 flex-wrap items-center justify-end gap-x-2 gap-y-1"><span className="min-w-0 break-words">{date ? `查询 ${date}` : '最新公布快照'}</span><button type="button" aria-label="刷新ETF份额" title="刷新ETF份额" disabled={query.isFetching} onClick={() => { void query.refetch() }} className="inline-flex min-h-9 w-9 shrink-0 items-center justify-center rounded hover:bg-elevated disabled:opacity-40 sm:min-h-7 sm:w-7"><RefreshCw className={`h-3 w-3 ${query.isFetching ? 'animate-spin' : ''}`} /></button></span>} />
    {query.isPending && <p role="status" className="text-xs text-muted">正在读取交易所公布份额…</p>}
    {error && <p role="status" className="break-words text-xs text-warning">{data?.stale ? '旧缓存 · ' : ''}{error}</p>}
    {data?.shares_10k != null && <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
      <span className="text-xs font-medium text-foreground">{data.name}</span>
      <span className="font-mono text-lg text-foreground">{data.shares_10k.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}<span className="ml-1 text-xs text-muted">万份</span></span>
      <span className="text-[11px] text-muted">数据日 {data.date}{data.cached ? ' · 缓存' : ''}</span>
    </div>}
    <p className="mt-1.5 text-[10px] text-muted">官方披露的份额数量，非基金净资产、成交量或资金净流入。可用日期范围以披露记录为准；尚未发布时会提示。</p>
  </section>
}
