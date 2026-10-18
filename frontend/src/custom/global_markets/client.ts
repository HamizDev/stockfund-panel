import { api } from '@/lib/api'
import type { GlobalMarket, GlobalMarketChartInterval } from './types'

/** 页面只通过统一 api 请求封装访问后端。 */
export const globalMarketClient = {
  overview: (market: GlobalMarket, signal?: AbortSignal) => api.globalMarketOverview(market, signal),
  catalog: (market: GlobalMarket, q: string, signal?: AbortSignal) => api.globalMarketCatalog(market, q, signal),
  chart: (market: GlobalMarket, symbol: string, interval: GlobalMarketChartInterval, signal?: AbortSignal) =>
    api.globalMarketChart(market, symbol, interval, signal),
  resources: (signal?: AbortSignal) => api.globalMarketResources(signal),
}
