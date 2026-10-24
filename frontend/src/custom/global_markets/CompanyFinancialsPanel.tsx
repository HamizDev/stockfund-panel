import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { INSTRUMENT_VIEW_GC_MS } from '@/lib/useInstrumentViewCache'
import type { GlobalFinancialTable, GlobalMarket } from './types'
import { canonicalFinancialSymbol } from './marketFormatters'

const TABLES: { value: GlobalFinancialTable; label: string }[] = [
  { value: 'metrics', label: '关键指标' }, { value: 'income', label: '利润表' },
  { value: 'balance', label: '资产负债' }, { value: 'cashflow', label: '现金流' },
]

function valueText(value: number | null, unit: string | null): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const number = Math.abs(value) >= 1e8 ? `${(value / 1e8).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}亿`
    : Math.abs(value) >= 1e4 ? `${(value / 1e4).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}万`
      : value.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
  return `${number}${unit ? ` ${unit}` : ''}`
}

/** Independent, opt-in query: a slow optional report never blocks the price chart. */
export function CompanyFinancialsPanel({ market, symbol, kind }: {
  market: GlobalMarket; symbol: string; kind?: 'stock' | 'etf' | 'index'
}) {
  const [expanded, setExpanded] = useState(false)
  const [table, setTable] = useState<GlobalFinancialTable>('metrics')
  const applicable = kind === 'stock'
  const query = useQuery({
    queryKey: QK.globalFinancials(market, symbol, table),
    queryFn: ({ signal }) => api.globalFinancials(market, symbol, table, signal),
    enabled: applicable && expanded,
    staleTime: query => query.state.data?.stale ? 15_000
      : query.state.data?.items.length ? 24 * 60 * 60_000 : 60_000,
    gcTime: INSTRUMENT_VIEW_GC_MS,
    refetchOnWindowFocus: false,
    retry: false,
    meta: { instrumentView: `global-market-${market}`, symbol },
  })
  const result = query.data?.market === market && query.data.symbol === canonicalFinancialSymbol(market, symbol) && query.data.table === table
    ? query.data : undefined

  return (
    <section aria-label="公司财务" className="rounded-card border border-border/60 bg-surface/80 p-2.5 sm:p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xs font-semibold text-foreground">公司财务</h2>
        {applicable && <button type="button" aria-expanded={expanded}
          onClick={() => setExpanded(value => !value)}
          className="min-h-8 rounded-btn border border-border bg-base/40 px-2.5 py-1 text-[11px] text-secondary hover:text-accent">
          {expanded ? '收起财务' : result ? '查看缓存财务' : '查看财务'}
        </button>}
      </div>
      {!applicable ? <p className="mt-2 text-[11px] text-muted">
        {kind === 'etf' ? 'ETF 不适用上市公司财务报表。' : kind === 'index'
          ? '指数不适用上市公司财务报表。' : '确认标的类型后可查询公司财务。'}
      </p> : !expanded ? <p className="mt-1.5 text-[10px] text-muted">按需读取所选标的，切页保留缓存；不会影响主图加载。</p> : <>
        <div role="group" aria-label="财务报表类型" className="mt-2.5 flex flex-wrap gap-1.5">
          {TABLES.map(item => <button key={item.value} type="button" aria-pressed={table === item.value}
            onClick={() => setTable(item.value)}
            className={`min-h-8 rounded-btn border px-2.5 py-1 text-[11px] ${table === item.value
              ? 'border-accent/40 bg-accent/10 text-accent' : 'border-border bg-base/40 text-secondary'}`}>
            {item.label}
          </button>)}
        </div>
        {query.isFetching && <p role="status" className="mt-2 text-xs text-muted">{result ? '正在更新财务…' : '正在读取所选报表…'}</p>}
        {query.isError && <div role="alert" className="mt-2 text-xs text-warning">
          财务读取失败：{query.error instanceof Error ? query.error.message : '请稍后重试'}
          <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}
            className="ml-2 min-h-8 rounded-btn border border-border px-2 text-accent">重试</button>
        </div>}
        {result?.stale && <p role="status" className="mt-2 text-[11px] text-warning">当前为过期财务缓存。</p>}
        {result?.warnings.map((warning, index) => <p key={index} className="mt-1.5 text-[10px] leading-4 text-warning">{warning}</p>)}
        {result && !result.items.length && <p role="status" className="mt-2 text-xs text-muted">暂未取得这张报表，缺失值不按零处理。</p>}
        {result?.items.map((report, index) => <details key={`${report.period_end}:${report.period_start}:${report.report_type}`} open={index === 0}
          className="mt-2 rounded-md border border-border/60 bg-base/40 px-2.5 py-2">
          <summary className="cursor-pointer text-[11px] font-medium text-secondary">报告期 {report.period_end}{report.report_type ? ` · ${report.report_type}` : ''}</summary>
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted">
            <span>公告日：{report.published_at || '未提供'}</span>
            {report.period_start && <span>期间：{report.period_start} 至 {report.period_end}</span>}
            {report.report_label && <span>{report.report_label}</span>}
            <span>报表货币：{report.currency || '未提供'}</span>
            <span>会计口径：{report.accounting_standard || '未提供'}</span>
          </div>
          <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {report.values.map(item => <div key={item.key} className="min-w-0 rounded border border-border/50 p-2">
              <dt className="text-[10px] text-muted">{item.label}</dt>
              <dd className="mt-0.5 break-words text-xs font-semibold tabular-nums text-foreground">{valueText(item.value, item.unit)}</dd>
            </div>)}
          </dl>
        </details>)}
        {result && <p className="mt-2 text-[10px] leading-4 text-muted">报表期末和公告日分别显示；公告日缺失的报表不能用作历史时点已知信息。获取于 {new Date(result.fetched_at_ms).toLocaleString('zh-CN')}。</p>}
      </>}
    </section>
  )
}
