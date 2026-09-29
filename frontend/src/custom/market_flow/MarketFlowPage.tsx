/**
 * 市场页面 — 人气排行 / 资金流向 / 主力资金。
 *
 * 自包含扩展页面: 数据全部走 /api/custom/market-flow, 状态用组件内 state。
 * 删除本目录即卸载。
 *
 * 样式: 与主面板 Dashboard 一致 (accent 色条标题 / surface 卡片 / 紧凑字号)。
 */
import { useEffect, useState } from 'react'
import { Activity, Flame, Droplets, Zap } from 'lucide-react'
import { cn } from '@/lib/cn'
import {
  marketFlowApi,
  pctText,
  yuanToYi,
  type PopularityItem,
  type MoneyFlowItem,
  type MoneyFlowMainItem,
} from './client'

type TabId = 'popularity' | 'money-flow' | 'money-flow-main'

const TABS: { id: TabId; label: string; icon: typeof Flame }[] = [
  { id: 'popularity', label: '人气排行', icon: Flame },
  { id: 'money-flow', label: '资金流向', icon: Droplets },
  { id: 'money-flow-main', label: '主力资金', icon: Zap },
]

function SectionTitle({ icon: Icon, title, extra }: { icon: typeof Activity; title: string; extra?: string }) {
  return (
    <div className="mb-1.5 flex items-center gap-1.5">
      <span className="h-3 w-0.5 rounded-full bg-gradient-to-b from-accent to-accent/30" />
      <Icon className="h-3.5 w-3.5 text-accent" />
      <h2 className="text-xs font-semibold text-foreground">{title}</h2>
      {extra && <span className="ml-auto text-[10px] text-muted-foreground">{extra}</span>}
    </div>
  )
}

function RankCell({ rank }: { rank: number | null }) {
  if (rank === null || rank === undefined) return <span className="text-muted-foreground">—</span>
  const hot = rank <= 3
  return (
    <span
      className={cn(
        'inline-block min-w-6 rounded px-1 text-center font-mono text-[11px]',
        hot ? 'bg-accent/20 font-semibold text-accent' : 'text-muted-foreground',
      )}
    >
      {rank}
    </span>
  )
}

function ChgCell({ v }: { v: number | null | undefined }) {
  const txt = pctText(v)
  const cls = v === null || v === undefined ? 'text-muted-foreground' : v >= 0 ? 'text-bull' : 'text-bear'
  return <span className={cn('font-mono text-[11px]', cls)}>{txt}</span>
}

function PopularityTable({ items }: { items: PopularityItem[] }) {
  return (
    <table className="w-full text-[11px]">
      <thead>
        <tr className="border-b border-border/40 text-left text-muted-foreground">
          <th className="py-1.5 pr-2 font-medium">排名</th>
          <th className="py-1.5 pr-2 font-medium">代码</th>
          <th className="py-1.5 pr-2 font-medium">名称</th>
          <th className="py-1.5 pr-2 text-right font-medium">热度</th>
          <th className="py-1.5 text-right font-medium">涨跌幅</th>
        </tr>
      </thead>
      <tbody>
        {items.map((it) => (
          <tr key={it.symbol} className="border-b border-border/20 hover:bg-muted/30">
            <td className="py-1.5 pr-2"><RankCell rank={it.rank} /></td>
            <td className="py-1.5 pr-2 font-mono text-muted-foreground">{it.symbol}</td>
            <td className="py-1.5 pr-2 text-foreground">{it.name ?? '—'}</td>
            <td className="py-1.5 pr-2 text-right font-mono text-foreground">
              {it.heat !== null && it.heat !== undefined ? it.heat.toFixed(1) : '—'}
            </td>
            <td className="py-1.5 text-right"><ChgCell v={it.change_pct} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function MoneyFlowTable({ items, main }: { items: (MoneyFlowItem | MoneyFlowMainItem)[]; main?: boolean }) {
  return (
    <table className="w-full text-[11px]">
      <thead>
        <tr className="border-b border-border/40 text-left text-muted-foreground">
          <th className="py-1.5 pr-2 font-medium">排名</th>
          <th className="py-1.5 pr-2 font-medium">代码</th>
          <th className="py-1.5 pr-2 font-medium">名称</th>
          <th className="py-1.5 pr-2 text-right font-medium">净流入</th>
          <th className="py-1.5 pr-2 text-right font-medium">{main ? '主力买入' : '流入'}</th>
          <th className="py-1.5 pr-2 text-right font-medium">{main ? '主力卖出' : '流出'}</th>
          <th className="py-1.5 text-right font-medium">涨跌幅</th>
        </tr>
      </thead>
      <tbody>
        {items.map((it) => {
          const inflow = main ? (it as MoneyFlowMainItem).buy : (it as MoneyFlowItem).inflow
          const outflow = main ? (it as MoneyFlowMainItem).sell : (it as MoneyFlowItem).outflow
          const netCls = it.net === null || it.net === undefined ? 'text-muted-foreground' : it.net >= 0 ? 'text-bull' : 'text-bear'
          return (
            <tr key={it.symbol} className="border-b border-border/20 hover:bg-muted/30">
              <td className="py-1.5 pr-2"><RankCell rank={it.rank} /></td>
              <td className="py-1.5 pr-2 font-mono text-muted-foreground">{it.symbol}</td>
              <td className="py-1.5 pr-2 text-foreground">{it.name ?? '—'}</td>
              <td className={cn('py-1.5 pr-2 text-right font-mono', netCls)}>{yuanToYi(it.net)}</td>
              <td className="py-1.5 pr-2 text-right font-mono text-muted-foreground">{yuanToYi(inflow)}</td>
              <td className="py-1.5 pr-2 text-right font-mono text-muted-foreground">{yuanToYi(outflow)}</td>
              <td className="py-1.5 text-right"><ChgCell v={it.change_pct} /></td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export function MarketFlowPage() {
  const [tab, setTab] = useState<TabId>('popularity')
  const [items, setItems] = useState<(PopularityItem | MoneyFlowItem | MoneyFlowMainItem)[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError('')
    const load = async () => {
      try {
        let r: { items: (PopularityItem | MoneyFlowItem | MoneyFlowMainItem)[] }
        if (tab === 'popularity') r = await marketFlowApi.popularity(100)
        else if (tab === 'money-flow') r = await marketFlowApi.moneyFlow(100)
        else r = await marketFlowApi.moneyFlowMain(100)
        if (alive) setItems(r.items)
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : '加载失败')
      } finally {
        if (alive) setLoading(false)
      }
    }
    load()
    return () => {
      alive = false
    }
  }, [tab])

  const activeTab = TABS.find((t) => t.id === tab)!

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              'flex items-center gap-1 rounded-md px-2.5 py-1.5 text-[11px] font-medium',
              tab === t.id
                ? 'bg-accent/15 text-accent'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground',
            )}
          >
            <t.icon className="h-3 w-3" />
            {t.label}
          </button>
        ))}
      </div>

      <div className="rounded-md border border-border/40 bg-surface/60 p-2.5">
        <SectionTitle icon={activeTab.icon} title={activeTab.label} extra="数据来自 shy313.com，低频更新" />
        {loading && <div className="py-8 text-center text-[11px] text-muted-foreground">加载中…</div>}
        {error && <div className="py-8 text-center text-[11px] text-bear">{error}</div>}
        {!loading && !error && items.length === 0 && (
          <div className="py-8 text-center text-[11px] text-muted-foreground">暂无数据</div>
        )}
        {!loading && !error && items.length > 0 && (
          <div className="max-h-[70vh] overflow-y-auto">
            {tab === 'popularity' && <PopularityTable items={items as PopularityItem[]} />}
            {tab === 'money-flow' && <MoneyFlowTable items={items as MoneyFlowItem[]} />}
            {tab === 'money-flow-main' && <MoneyFlowTable items={items as MoneyFlowMainItem[]} main />}
          </div>
        )}
      </div>
    </div>
  )
}
