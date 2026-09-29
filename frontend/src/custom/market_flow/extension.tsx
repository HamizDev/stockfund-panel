/**
 * 市场数据前端扩展 — 完全解耦模块。
 *
 * 注册 /market 路由 + 侧边栏"市场"导航 + 个股预览底部概念/行业标签,
 * 不修改任何核心文件。删除本目录即整体卸载。apiVersion: 1。
 */
import { useEffect, useState } from 'react'
import { TrendingUp } from 'lucide-react'
import type { FrontendExtension, FrontendSlotContextMap } from '@/extensions/types'
import { MarketFlowPage } from './MarketFlowPage'
import { marketFlowApi, yuanToYi, type MemberItem, type StockFlow } from './client'

type AnySlotContext = FrontendSlotContextMap[keyof FrontendSlotContextMap]
type StockPreviewContext = FrontendSlotContextMap['stock-preview.footer']

/** 6 位代码 → 带后缀格式 (600900 → 600900.SH) */
function toSuffixed(symbol: string): string {
  const s = symbol.trim()
  if (/^[68]/.test(s)) return `${s}.SH`
  if (/^[03]/.test(s)) return `${s}.SZ`
  if (/^[49]/.test(s)) return `${s}.BJ`
  return s
}

/** 个股预览底部: 人气/资金/主力 + 概念/行业标签 (标签可点看成分股) */
function StockConcepts(props: AnySlotContext) {
  const { symbol } = props as StockPreviewContext
  const [flow, setFlow] = useState<StockFlow | null>(null)
  const [members, setMembers] = useState<{ title: string; items: MemberItem[] } | null>(null)
  const [membersLoading, setMembersLoading] = useState(false)

  useEffect(() => {
    let alive = true
    setFlow(null)
    marketFlowApi.stockFlow(toSuffixed(symbol)).then((r) => alive && setFlow(r)).catch(() => {})
    return () => {
      alive = false
    }
  }, [symbol])

  const openMembers = (kind: 'concept' | 'industry', name: string) => {
    setMembersLoading(true)
    setMembers({ title: name, items: [] })
    const p = kind === 'concept' ? marketFlowApi.conceptMembers(name) : marketFlowApi.industryMembers(name)
    p.then((r) => setMembers({ title: name, items: r.items }))
      .catch(() => setMembers({ title: name, items: [] }))
      .finally(() => setMembersLoading(false))
  }

  if (!flow) return null
  const { popularity, money_flow, money_flow_main, concepts, industries } = flow
  const hasFlow = popularity || money_flow || money_flow_main
  if (!hasFlow && concepts.length === 0 && industries.length === 0) return null

  const moneyCls = (v: number | null | undefined) =>
    v === null || v === undefined ? 'text-muted-foreground' : v >= 0 ? 'text-bull' : 'text-bear'

  return (
    <div className="space-y-1 px-1 pb-1">
      {hasFlow && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px]">
          {popularity && (
            <span className="text-muted-foreground">
              人气 <span className="font-mono text-foreground">#{popularity.rank ?? '—'}</span>
              <span className="ml-1 font-mono text-muted-foreground">{popularity.heat?.toFixed(0) ?? ''}</span>
            </span>
          )}
          {money_flow && (
            <span className="text-muted-foreground">
              资金流向 <span className={`font-mono ${moneyCls(money_flow.net)}`}>{yuanToYi(money_flow.net)}</span>
            </span>
          )}
          {money_flow_main && (
            <span className="text-muted-foreground">
              主力 <span className={`font-mono ${moneyCls(money_flow_main.net)}`}>{yuanToYi(money_flow_main.net)}</span>
            </span>
          )}
        </div>
      )}
      {industries.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[10px] text-muted-foreground">行业:</span>
          {industries.slice(0, 5).map((ind) => (
            <button
              key={ind}
              onClick={() => openMembers('industry', ind)}
              className="rounded border border-accent/25 bg-accent/10 px-1.5 py-px text-[10px] text-accent hover:bg-accent/20"
            >
              {ind}
            </button>
          ))}
        </div>
      )}
      {concepts.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[10px] text-muted-foreground">概念:</span>
          {concepts.slice(0, 8).map((c) => (
            <button
              key={c}
              onClick={() => openMembers('concept', c)}
              className="rounded border border-border/40 bg-muted/40 px-1.5 py-px text-[10px] text-foreground hover:bg-muted"
            >
              {c}
            </button>
          ))}
        </div>
      )}
      {members && (
        <div className="rounded border border-border/40 bg-muted/20 p-1.5">
          <div className="mb-1 flex items-center">
            <span className="text-[10px] font-medium text-foreground">{members.title} 成分股</span>
            <button
              onClick={() => setMembers(null)}
              className="ml-auto text-[10px] text-muted-foreground hover:text-foreground"
            >
              关闭
            </button>
          </div>
          {membersLoading ? (
            <div className="py-2 text-center text-[10px] text-muted-foreground">加载中…</div>
          ) : members.items.length === 0 ? (
            <div className="py-2 text-center text-[10px] text-muted-foreground">暂无数据</div>
          ) : (
            <div className="max-h-32 overflow-y-auto">
              <div className="flex flex-wrap gap-1">
                {members.items.slice(0, 60).map((m) => (
                  <span
                    key={m.symbol}
                    className="rounded bg-muted/60 px-1.5 py-px font-mono text-[10px] text-foreground"
                    title={m.name ?? ''}
                  >
                    {m.name ?? m.symbol}
                  </span>
                ))}
              </div>
              {members.items.length > 60 && (
                <div className="mt-1 text-[10px] text-muted-foreground">共 {members.items.length} 只，仅显示前 60</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const extension: FrontendExtension = {
  id: 'market.flow',
  apiVersion: 1,
  routes: [{ id: 'market-flow', path: '/market', component: MarketFlowPage }],
  navigation: [
    {
      id: 'market-flow',
      routeId: 'market-flow',
      label: '市场',
      icon: TrendingUp,
      order: 310,
    },
  ],
  slots: [
    {
      name: 'stock-preview.footer',
      id: 'market-flow-concepts',
      order: 100,
      component: StockConcepts,
    },
  ],
}

export default extension
