import { skipToken, useQuery } from '@tanstack/react-query'
import { Database, RefreshCw } from 'lucide-react'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import type { KronosAssetType, KronosSelection } from './state'

interface Props {
  selection: KronosSelection | null
  assetType?: KronosAssetType
  onSelect: (symbol: string, name: string, assetType?: 'stock' | 'etf' | 'index') => void
}

export function KronosTrainingCoverage({ selection, assetType, onSelect }: Props) {
  const symbol = selection?.symbol
  const canCheck = Boolean(symbol && assetType)
  const query = useQuery({
    queryKey: QK.kronosTrainingCoverage(symbol, assetType),
    queryFn: symbol && assetType ? ({ signal }) => api.kronosTrainingCoverage(symbol, assetType, signal) : skipToken,
    staleTime: 60_000,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const data = query.data
  const remaining = data ? data.target_days - data.complete_days : 0

  return (
    <section aria-label="训练样本覆盖" className="min-w-0 space-y-3 rounded-md border border-border/40 bg-surface/70 p-3 sm:p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
          <Database aria-hidden="true" className="h-3.5 w-3.5 text-accent" />Base 训练样本覆盖
        </h3>
        <button type="button" onClick={() => { void query.refetch() }} disabled={!canCheck || query.isFetching}
          className="inline-flex h-7 items-center gap-1 rounded border border-border/50 px-2 text-[10px] text-secondary disabled:opacity-50">
          <RefreshCw aria-hidden="true" className="h-3 w-3" />重新检查
        </button>
      </div>
      <div className="max-w-sm">
        <StockFinancialSearch onSelect={onSelect} assetTypes="stock,etf" placeholder="输入股票或 ETF 代码 / 名称" />
      </div>
      {selection && <p className="break-words text-xs text-secondary">{selection.name} · {symbol}</p>}
      {!canCheck && <p className="text-xs text-muted">请选择股票或 ETF，以检查本地分钟数据。</p>}
      {canCheck && query.isPending && <p role="status" className="text-xs text-muted">正在核验本地分钟数据…</p>}
      {query.isError && <p role="alert" className="text-xs text-warning">{data ? '检查失败，显示上次结果。' : '本地样本覆盖读取失败，可重新检查。'}</p>}
      {data && (
        <div className="space-y-2 rounded border border-border/40 bg-elevated/25 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span><span className="mr-1.5 font-mono font-semibold text-accent">{data.complete_days} / {data.target_days}</span>完整交易日</span>
            <span className={remaining ? 'text-warning' : 'text-secondary'}>
              {remaining ? `距样本目标还差 ${remaining} 个交易日` : '已达样本数量目标'}
            </span>
          </div>
          <div role="progressbar" aria-label="完整交易日" aria-valuenow={data.complete_days} aria-valuemax={data.target_days}
            className="h-1.5 overflow-hidden rounded-full bg-border/50">
            <div className="h-full rounded-full bg-accent" style={{ width: `${data.complete_days / data.target_days * 100}%` }} />
          </div>
          <p className="break-words text-[11px] text-secondary">核验区间 {data.from_date} 至 {data.through_date}</p>
          {data.dates.length > 0
            ? <details className="text-[11px] text-muted"><summary className="cursor-pointer">查看完整日期</summary><p className="break-words pt-1">{data.dates.join(' · ')}</p></details>
            : <p className="text-[11px] text-muted">该区间内没有通过核验的完整交易日。</p>}
        </div>
      )}
      <p className="text-[11px] leading-5 text-muted">只检查本机已保存的分钟 K。每个完整交易日需有 240 根有效分钟 K。模型训练未启用。</p>
    </section>
  )
}
