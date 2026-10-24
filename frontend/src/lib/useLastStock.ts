import { useCallback, useRef, useState } from 'react'
import { storage } from './storage'

/**
 * 记忆"上次查看的个股"(按页面维度,localStorage 持久化)。
 *
 * 两个分析页(财务 / 个股)各自独立记忆,key 区分:
 *   - financials: 最后查看的财务分析个股
 *   - stock-analysis: 最后查看的个股分析个股
 *
 * 用法:
 *   const { last, remember } = useLastStock('stock-analysis')
 *   remember('000001.SZ', '平安银行')   // 选中股票时调用
 *   <LastStockChip stock={last} ... />  // 渲染在 PageHeader 右侧
 */

export interface StockRef { symbol: string; name: string }

const PREFIX = 'last_stock:'
export const MAX_RECENT_STOCKS = 6

type StockHistory = { scope: string; last: StockRef | null; recent: StockRef[] }

function validRef(value: unknown): StockRef | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const item = value as Record<string, unknown>
  if (typeof item.symbol !== 'string' || typeof item.name !== 'string') return null
  const symbol = item.symbol.trim()
  if (!symbol || symbol.length > 40 || /[\s\u0000-\u001f]/.test(symbol)) return null
  return { symbol, name: item.name.trim().slice(0, 120) || symbol }
}

function loadHistory(scope: string): StockHistory {
  const last = load(scope)
  const saved = storage.recentStocks(scope).get(null)
  const recent: StockRef[] = []
  // A valid empty list means the user cleared it. Old single-stock storage
  // remains the fallback for browsers that have not used recent selections.
  const candidates = Array.isArray(saved) ? saved.slice(0, 50) : last ? [last] : []
  for (const candidate of candidates) {
    const ref = validRef(candidate)
    if (ref && !recent.some(item => item.symbol === ref.symbol)) recent.push(ref)
    if (recent.length === MAX_RECENT_STOCKS) break
  }
  return { scope, last, recent }
}

export function useLastStock(scope: string) {
  const [stored, setStored] = useState<StockHistory>(() => loadHistory(scope))
  const history = stored.scope === scope ? stored : loadHistory(scope)
  const current = useRef(history)
  current.current = history

  const commit = useCallback((next: StockHistory) => {
    current.current = next
    setStored(next)
    save(scope, next.last)
    storage.recentStocks(scope).set(next.recent)
  }, [scope])

  const remember = useCallback((symbol: string, name: string) => {
    const incoming = validRef({ symbol, name })
    if (!incoming) return
    const previous = current.current
    const known = previous.recent.find(item => item.symbol === incoming.symbol)
    const ref = incoming.name === incoming.symbol && known ? known : incoming
    if (previous.last?.symbol === ref.symbol && previous.last.name === ref.name
      && previous.recent[0]?.symbol === ref.symbol && previous.recent[0].name === ref.name) return
    commit({ scope, last: ref, recent: [ref, ...previous.recent.filter(item => item.symbol !== ref.symbol)].slice(0, MAX_RECENT_STOCKS) })
  }, [scope, commit])

  const clear = useCallback(() => {
    commit({ scope, last: null, recent: [] })
  }, [scope, commit])

  const clearRecent = useCallback(() => {
    commit({ ...current.current, recent: [] })
  }, [commit])

  return { last: history.last, recent: history.recent, remember, clear, clearRecent }
}

function load(scope: string): StockRef | null {
  try {
    const v = localStorage.getItem(PREFIX + scope)
    if (!v) return null
    return validRef(JSON.parse(v))
  } catch { /* ignore */ }
  return null
}

function save(scope: string, ref: StockRef | null) {
  try {
    if (ref) localStorage.setItem(PREFIX + scope, JSON.stringify(ref))
    else localStorage.removeItem(PREFIX + scope)
  } catch { /* ignore */ }
}
