import type { OHLC } from '@/components/EChartsCandlestick'

export interface ResearchMetrics {
  adx: number | null
  plusDI: number | null
  minusDI: number | null
  atrRatio: number | null
  bollWidth: number | null
  bollPosition: number | null
}

export function researchMetrics(row?: OHLC): ResearchMetrics {
  const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
  const positive = (value: unknown): value is number => finite(value) && value > 0
  const adx = finite(row?.adx_14) && row.adx_14 >= 0 && row.adx_14 <= 100 ? row.adx_14 : null
  const plusDI = finite(row?.adx_plus_di_14) && row.adx_plus_di_14 >= 0 ? row.adx_plus_di_14 : null
  const minusDI = finite(row?.adx_minus_di_14) && row.adx_minus_di_14 >= 0 ? row.adx_minus_di_14 : null
  const atrRatio = finite(row?.atr_14) && row.atr_14 >= 0 && positive(row.close) ? row.atr_14 / row.close : null
  const width = finite(row?.boll_upper) && finite(row?.boll_lower) ? row.boll_upper - row.boll_lower : null
  return {
    adx, plusDI, minusDI, atrRatio,
    bollWidth: width != null && width >= 0 && positive(row?.ma20) ? width / row.ma20 : null,
    bollPosition: width != null && width > 0 && finite(row?.close) && finite(row?.boll_lower)
      ? (row.close - row.boll_lower) / width : null,
  }
}

/** An estimate on the chart's adjusted-price basis; never mixes raw turnover amount with adjusted OHLC. */
export function anchoredVwap(data: readonly OHLC[], anchor: string): {
  values: Array<number | null>; error: string | null
} {
  const values: Array<number | null> = Array(data.length).fill(null)
  if (!anchor) return { values, error: null }
  const start = data.findIndex(row => row.date === anchor)
  if (start < 0) return { values, error: '起点不在已加载日K中，请扩大日期范围。' }
  let totalVolume = 0
  let totalPV = 0
  for (let i = start; i < data.length; i++) {
    const row = data[i]
    if (row.volume == null || !Number.isFinite(row.volume) || row.volume < 0
      || ![row.high, row.low, row.close].every(value => Number.isFinite(value) && value > 0)) {
      return { values, error: `${row.date}量价数据缺失，锚定线从该日停止，不能跳过缺失量继续累计。` }
    }
    totalPV += (row.high + row.low + row.close) / 3 * row.volume
    totalVolume += row.volume
    values[i] = totalVolume > 0 ? totalPV / totalVolume : null
  }
  return { values, error: totalVolume === 0 ? '锚定区间没有有效成交量。' : null }
}

export function trendDescription(metrics: ResearchMetrics): string {
  if (metrics.adx == null || metrics.plusDI == null || metrics.minusDI == null) return '历史不足或数据不可计算'
  const strength = metrics.adx >= 25 ? '趋势较强' : metrics.adx < 20 ? '趋势较弱' : '趋势过渡'
  const direction = metrics.plusDI > metrics.minusDI ? '上行动向占优'
    : metrics.minusDI > metrics.plusDI ? '下行动向占优' : '方向均衡'
  return `${strength} · ${direction}`
}

export function chartResearchPrompt(symbol: string, row: OHLC, metrics: ResearchMetrics, anchor: string, value: number | null): string {
  return `请解读${symbol}的日线研究快照（${row.date}${row.is_live ? '，未收盘，指标可能变化' : ''}）。\n`
    + `图表价格为前复权口径；比例字段为小数，0.01表示1%。\n`
    + JSON.stringify({ close: row.close, ...metrics, anchor_date: anchor || null, anchored_vwap_estimate: value })
    + '\nADX只描述强度，方向需结合+DI/-DI；%B允许超出0至1。锚定均价是典型价×成交量估算，不是主力成本。'
    + '\n结合趋势、波动、量价解释适合研究的情景、失效条件与还缺的数据。明确区分指标事实和推测，不虚构回测成绩、新闻或成交。'
    + '\n如果涉及ETF做T，请先核对品种T+0/T+1属性、可卖底仓和账户实际费用；不要仅凭超买超卖就给出确定买卖结论。此请求只做研究解读。'
}
