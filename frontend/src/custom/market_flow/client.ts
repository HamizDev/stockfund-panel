/**
 * 市场数据自包含 HTTP 客户端。
 *
 * 取舍同 fund 扩展: 不修改核心 lib/api.ts, 在此内建最小客户端。
 * 后端见 backend/app/custom/market_flow/routes.py。
 */

export interface PopularityItem {
  rank: number | null
  symbol: string
  name: string | null
  heat: number | null
  /** 小数制, 0.0174 = +1.74% */
  change_pct: number | null
}

export interface MoneyFlowItem {
  rank: number | null
  symbol: string
  name: string | null
  /** 元 */
  net: number | null
  inflow: number | null
  outflow: number | null
  change_pct: number | null
}

export interface MoneyFlowMainItem {
  rank: number | null
  symbol: string
  name: string | null
  net: number | null
  buy: number | null
  sell: number | null
  change_pct: number | null
}

export interface StockFlow {
  symbol: string
  popularity: { rank: number | null; heat: number | null; change_pct: number | null } | null
  money_flow: { rank: number | null; net: number | null; inflow: number | null; outflow: number | null; change_pct: number | null } | null
  money_flow_main: { rank: number | null; net: number | null; buy: number | null; sell: number | null; change_pct: number | null } | null
  concepts: string[]
  industries: string[]
}

export interface MemberItem {
  symbol: string
  name: string | null
}

async function req<T>(path: string): Promise<T> {
  const res = await fetch(`/api/custom/market-flow${path}`, { credentials: 'same-origin' })
  if (!res.ok) {
    let detail = ''
    try {
      const j = await res.json()
      detail = j.detail ?? ''
    } catch {
      /* ignore */
    }
    throw new Error(detail || `请求失败 ${res.status}`)
  }
  return res.json()
}

export const marketFlowApi = {
  popularity(limit = 100): Promise<{ items: PopularityItem[] }> {
    return req(`/popularity?limit=${limit}`)
  },
  moneyFlow(limit = 100): Promise<{ items: MoneyFlowItem[] }> {
    return req(`/money-flow?limit=${limit}`)
  },
  moneyFlowMain(limit = 100): Promise<{ items: MoneyFlowMainItem[] }> {
    return req(`/money-flow-main?limit=${limit}`)
  },
  concepts(symbol: string): Promise<{ symbol: string; concepts: string[] }> {
    return req(`/concepts/${encodeURIComponent(symbol)}`)
  },
  industries(symbol: string): Promise<{ symbol: string; industries: string[] }> {
    return req(`/industries/${encodeURIComponent(symbol)}`)
  },
  stockFlow(symbol: string): Promise<StockFlow> {
    return req(`/stock/${encodeURIComponent(symbol)}`)
  },
  conceptMembers(concept: string): Promise<{ concept: string; items: MemberItem[] }> {
    return req(`/concept-members?concept=${encodeURIComponent(concept)}`)
  },
  industryMembers(industry: string): Promise<{ industry: string; items: MemberItem[] }> {
    return req(`/industry-members?industry=${encodeURIComponent(industry)}`)
  },
}

/** 小数制 → 百分比文案 */
export function pctText(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const n = v * 100
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`
}

/** 元 → 亿元文案 */
export function yuanToYi(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const yi = v / 1e8
  return `${yi >= 0 ? '+' : ''}${yi.toFixed(digits)}亿`
}
