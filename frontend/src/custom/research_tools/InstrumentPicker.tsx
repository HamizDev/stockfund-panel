import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { api } from '@/lib/api'
import { RESEARCH_QUERY_KEYS } from '@/lib/queryKeys'

export function InstrumentPicker({ market, onSelect }: { market: 'cn' | 'hk' | 'us'; onSelect: (symbol: string, name: string) => void }) {
  const [query,setQuery]=useState('')
  const [term,setTerm]=useState('')
  useEffect(()=>{const timer=window.setTimeout(()=>setTerm(query.trim()),250);return()=>window.clearTimeout(timer)},[query])
  const result=useQuery({queryKey:RESEARCH_QUERY_KEYS.instrument(market,term),queryFn:({signal})=>api.globalMarketCatalog(market==='cn'?'hk':market,term,signal),enabled:market!=='cn' && !!term,staleTime:300_000,retry:false})
  if(market==='cn') return <StockFinancialSearch assetTypes="stock,etf" placeholder="搜索股票／ETF名称或代码" onSelect={onSelect} />
  return <div className="relative min-w-0 max-w-md"><input aria-label="搜索标的名称或代码" placeholder="输入中文名称或代码" className="w-full rounded-btn border border-border bg-surface px-3 py-2 text-sm" value={query} onChange={e=>setQuery(e.target.value)} />
    {term && <div className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded-card border border-border bg-surface p-1 shadow-lg">{result.isPending ? <p className="p-2 text-xs text-muted">查询中…</p>:result.isError?<p role="alert" className="p-2 text-xs text-warning">名称查询暂不可用</p>:result.data?.items.length ? result.data.items.slice(0,12).map(row=><button key={row.symbol} className="flex w-full justify-between gap-2 rounded p-2 text-left text-xs hover:bg-elevated" onClick={()=>{onSelect(row.symbol,row.name);setQuery('');setTerm('')}}><span>{row.name}</span><span className="text-muted">{row.symbol}</span></button>):<p className="p-2 text-xs text-muted">未找到对应标的</p>}</div>}
  </div>
}
