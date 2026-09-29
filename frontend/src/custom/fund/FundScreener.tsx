import { useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Zap, Shield, List } from 'lucide-react'
import { fundApi } from './client'

function Card({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-border bg-surface p-4">{children}</div>
}

const FUND_TYPES = ['股票型', '混合型', '指数型', '债券型']
const SHARE_OPTS = [
  { v: 'all', l: '全部' },
  { v: 'A', l: 'A类' },
  { v: 'C', l: 'C类' },
]
const SORT_OPTS = [
  { v: '1w', l: '近1周' },
  { v: '1m', l: '近1月' },
  { v: '3m', l: '近3月' },
  { v: '6m', l: '近6月' },
  { v: '1y', l: '近1年' },
  { v: '2y', l: '近2年' },
  { v: '3y', l: '近3年' },
]

export function FundScreener({ onSelect }: { onSelect: (code: string, name: string) => void }) {
  const [mode, setMode] = useState<'recommend' | 'manual'>('recommend')
  const [fundType, setFundType] = useState('股票型')
  const [share, setShare] = useState('all')
  const [sortBy, setSortBy] = useState('1y')

  const { data, isLoading, error } = useQuery({
    queryKey: ['fund-screener', mode, fundType, share, sortBy],
    queryFn: () =>
      fundApi.screener(fundType, {
        share,
        sortBy,
        limit: mode === 'recommend' ? 10 : 50,
        recommend: mode === 'recommend',
      }),
    staleTime: 6 * 3600 * 1000,
  })

  const fmtPct = (v: number | null) => {
    if (v === null || v === undefined) return '—'
    return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`
  }
  const pctColor = (v: number | null) =>
    v === null || v === undefined ? 'text-muted' : v > 0 ? 'text-bull' : v < 0 ? 'text-bear' : 'text-muted'

  const row = (it: any, idx: number, metrics: Array<{ l: string; v: number | null }>) => (
    <div
      key={it.code}
      className="cursor-pointer border-b border-border/20 py-2 last:border-0 hover:bg-elevated/40"
      onClick={() => onSelect(it.code, it.name)}
    >
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] text-muted">{idx + 1}</span>
        <span className="flex-1 truncate text-[12px] font-medium">{it.name}</span>
        {it.share_class && (
          <span className="rounded bg-elevated px-1 text-[10px] text-secondary">{it.share_class}类</span>
        )}
        <span className="font-mono text-[10px] text-muted">{it.code}</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 pl-5">
        {metrics.map((m) => (
          <span key={m.l} className="text-[10px] text-muted">
            {m.l} <span className={`font-mono ${pctColor(m.v)}`}>{fmtPct(m.v)}</span>
          </span>
        ))}
      </div>
      {it.reason && <div className="mt-0.5 pl-5 text-[10px] text-muted">{it.reason}</div>}
    </div>
  )

  return (
    <div className="space-y-2.5">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-1 rounded-md border border-border/40 p-0.5">
            <button
              onClick={() => setMode('recommend')}
              className={`rounded px-2.5 py-1 text-[11px] ${mode === 'recommend' ? 'bg-accent text-white' : 'text-muted'}`}
            >
              历史表现
            </button>
            <button
              onClick={() => setMode('manual')}
              className={`rounded px-2.5 py-1 text-[11px] ${mode === 'manual' ? 'bg-accent text-white' : 'text-muted'}`}
            >
              手动筛选
            </button>
          </div>
          <span className="text-[11px] text-muted">类型:</span>
          {FUND_TYPES.map((t) => (
            <button
              key={t}
              onClick={() => setFundType(t)}
              className={`rounded-md px-2 py-1 text-[11px] ${fundType === t ? 'bg-accent text-white' : 'border border-border/40'}`}
            >
              {t}
            </button>
          ))}
        </div>
        {mode === 'manual' && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="text-[11px] text-muted">份额:</span>
            {SHARE_OPTS.map((o) => (
              <button
                key={o.v}
                onClick={() => setShare(o.v)}
                className={`rounded-md px-2 py-1 text-[11px] ${share === o.v ? 'bg-accent text-white' : 'border border-border/40'}`}
              >
                {o.l}
              </button>
            ))}
            <span className="ml-1 text-[11px] text-muted">排序:</span>
            {SORT_OPTS.map((o) => (
              <button
                key={o.v}
                onClick={() => setSortBy(o.v)}
                className={`rounded-md px-2 py-1 text-[11px] ${sortBy === o.v ? 'bg-accent text-white' : 'border border-border/40'}`}
              >
                {o.l}
              </button>
            ))}
          </div>
        )}
        <div className="mt-1.5 text-[10px] text-muted">
          {mode === 'recommend'
            ? '短期按近1/3/6月历史收益加权，长期按近1/2/3年历史收益加权；这不是 AI 分析或未来收益预测。'
            : '按历史收益和份额类别筛选；不同基金的费用及风险需查看各自公告。'}
        </div>
      </Card>

      {isLoading && <div className="py-8 text-center text-[11px] text-muted">加载中...</div>}
      {error && <div role="alert" className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{error instanceof Error ? error.message : '基金榜单加载失败'}</div>}

      {data?.mode === 'recommend' && (
        <>
          <Card>
            <div className="mb-1 flex items-center gap-1.5">
              <Zap className="h-3.5 w-3.5 text-bull" />
              <span className="text-sm font-medium">短期历史表现</span>
              <span className="rounded bg-bull/10 px-1 py-px text-[10px] text-bull">C类</span>
            </div>
            {(data.short_term ?? []).map((it, i) =>
              row(it, i, [
                { l: '近1月', v: it.growth_1m },
                { l: '近3月', v: it.growth_3m },
                { l: '近6月', v: it.growth_6m },
              ]),
            )}
          </Card>
          <Card>
            <div className="mb-1 flex items-center gap-1.5">
              <Shield className="h-3.5 w-3.5 text-accent" />
              <span className="text-sm font-medium">长期历史表现</span>
              <span className="rounded bg-accent/10 px-1 py-px text-[10px] text-accent">A类</span>
            </div>
            {(data.long_term ?? []).map((it, i) =>
              row(it, i, [
                { l: '近1年', v: it.growth_1y },
                { l: '近2年', v: it.growth_2y },
                { l: '近3年', v: it.growth_3y },
              ]),
            )}
          </Card>
        </>
      )}

      {data?.mode === 'manual' && (
        <Card>
          <div className="mb-1 flex items-center gap-1.5">
            <List className="h-3.5 w-3.5" />
            <span className="text-[12px] font-medium">筛选结果</span>
            <span className="text-[10px] text-muted">共 {data.count} 只</span>
          </div>
          {(data.items ?? []).map((it, i) =>
            row(it, i, [
              { l: '近1月', v: it.growth_1m },
              { l: '近3月', v: it.growth_3m },
              { l: '近6月', v: it.growth_6m },
              { l: '近1年', v: it.growth_1y },
            ]),
          )}
        </Card>
      )}

      <div className="text-[10px] text-muted">
        数据：东方财富历史收益排名（经 AKShare）· 榜单未提供统计截止日期 · AI 选基是单独的模型分析页面
      </div>
    </div>
  )
}
