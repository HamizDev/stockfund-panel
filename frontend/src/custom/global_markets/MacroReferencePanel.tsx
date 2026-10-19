import { useQuery } from '@tanstack/react-query'
import { Landmark, RefreshCw } from 'lucide-react'
import { SectionTitle, dashboardPanel } from '@/components/dashboard/Primitives'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { MacroMarket, MacroReference } from './types'

const pct = (value: number | null) => value == null ? '—' : `${value.toFixed(2)}%`
const bp = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(1)} bp`

function YieldCurve({ data }: { data: MacroReference }) {
  const available = data.items.map((item, index) => ({ ...item, index })).filter(item => item.value != null)
  if (available.length < 2) return null
  const values = available.map(item => item.value!)
  const min = Math.min(...values) - 0.1
  const range = Math.max(0.2, Math.max(...values) - min + 0.1)
  const x = (index: number) => 28 + index * (544 / Math.max(1, data.items.length - 1))
  const y = (value: number) => 83 - (value - min) * 66 / range
  // Segment at missing tenors: never draw an invented yield through a gap.
  const segments: string[][] = [[]]
  data.items.forEach((item, index) => {
    if (item.value == null) { segments.push([]); return }
    segments.at(-1)!.push(`${x(index)},${y(item.value)}`)
  })
  return <svg viewBox="0 0 600 114" className="mt-2 w-full max-h-36 text-accent" role="img" aria-label={`美国国债收益率曲线，${data.date}，单位百分比，期限按等距排列`}>
    <path d="M28 86H572" stroke="currentColor" opacity="0.18" />
    {segments.filter(segment => segment.length > 1).map((segment, index) => <polyline key={index} points={segment.join(' ')} fill="none" stroke="currentColor" strokeWidth="2" />)}
    {available.map(item => <g key={item.id}><circle cx={x(item.index)} cy={y(item.value!)} r="2.5" fill="currentColor" /><text x={x(item.index)} y="105" textAnchor="middle" fill="currentColor" opacity="0.7" fontSize="10">{item.label}</text></g>)}
  </svg>
}

export function MacroReferencePanel({ market }: { market: MacroMarket }) {
  const query = useQuery({
    queryKey: QK.macroReference(market),
    queryFn: ({ signal }) => api.macroReference(market, signal),
    staleTime: 3600_000, gcTime: 3600_000, retry: false, refetchOnWindowFocus: false,
  })
  const data = query.data?.market === market ? query.data : undefined
  return <section className={`${dashboardPanel} mb-1.5`} aria-label={market === 'us' ? '美元利率参考' : '人民币资金利率'}>
    <SectionTitle icon={Landmark} title={market === 'us' ? '美债收益率 · 美元利率参考' : '人民币资金利率'} hint={<span className="flex items-center gap-2">
      <span>{data?.date ?? '等待官方数据'} · 日频</span>
      <button type="button" aria-label="刷新利率参考" disabled={query.isFetching} onClick={() => { void query.refetch() }} className="rounded p-1 hover:bg-elevated disabled:opacity-40"><RefreshCw className={`h-3 w-3 ${query.isFetching ? 'animate-spin' : ''}`} /></button>
    </span>} />
    {query.isPending && <p role="status" className="text-xs text-muted">正在读取官方利率…</p>}
    {(query.isError || data?.stale) && <p role="status" className="mb-1 text-xs text-warning">{data ? '旧缓存 · ' : ''}{data?.error || (query.error instanceof Error ? query.error.message : '官方利率暂不可用')}</p>}
    {data && <>
      <div className={`grid grid-cols-3 gap-1 ${market === 'us' ? 'sm:grid-cols-4 xl:grid-cols-8' : ''}`}>
        {data.items.map(item => <div key={item.id} className="min-w-0 rounded border border-border bg-elevated/40 px-2 py-1.5">
          <div className="text-[10px] text-muted">{item.label}</div>
          <div className="mt-1 font-mono text-sm tabular-nums text-foreground">{pct(item.value)}</div>
          <div className="mt-0.5 font-mono text-[10px] text-muted">{item.change_bp == null ? '变化 —' : `较前次 ${bp(item.change_bp)}`}</div>
        </div>)}
      </div>
      {market === 'us' && <YieldCurve data={data} />}
      <div className="mt-1.5 flex flex-wrap items-center justify-between gap-1 text-[10px] text-muted">
        <span><a href={data.source_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">{data.source}</a> · {data.date}{data.previous_date ? ` / 前次 ${data.previous_date}` : ''}{data.cached ? ' · 缓存' : ''}</span>
        {data.spread_bp != null && <span className="font-mono">10年 − 2年：{bp(data.spread_bp)}</span>}
      </div>
    </>}
    <p className="mt-1 text-[10px] text-muted">{market === 'us' ? '美国财政部公布的日度国债平价收益率；' : '银行间回购定盘利率，FR001 / FR007 / FR014；'}用于观察资金成本，非盘中行情。1 bp = 0.01个百分点。</p>
  </section>
}
