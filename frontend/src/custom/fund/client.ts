/**
 * 基金中心自包含 HTTP 客户端。
 *
 * 取舍同 assistant 扩展: 不修改核心 lib/api.ts, 在此内建同款约定的最小
 * 客户端 (同源 fetch + credentials + 统一错误文案)。后端见
 * backend/app/custom/fund/routes.py。
 */

export interface FundSearchItem {
  thscode: string
  ticker: string | null
  name: string | null
  asset_type: string
  kind_label: string
}

export interface FundPortfolioItem {
  thscode: string
  name?: string | null
  amount: number
  profit: number
}

export interface FundWatchItem {
  thscode: string
  name: string | null
  asset_type: string | null
  kind_label: string
  added_at: number
}

export interface FundQuote {
  thscode: string
  symbol: string
  name: string | null
  last_price: number | null
  prev_close: number | null
  open: number | null
  high: number | null
  low: number | null
  /** 小数制, 0.0174 = +1.74% */
  change_pct: number | null
  change_amount: number | null
  /** 手 */
  volume: number | null
  amount: number | null
  amplitude: number | null
  turnover_rate: number | null
  timestamp: number | null
}

export interface FundKBar {
  date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  volume: number | null
  amount: number | null
}

export interface FundNavPoint {
  nav_date: string
  unit_nav: number | null
  adj_nav: number | null
}

export type FundDataSource = 'fuyao' | 'akshare' | 'eastmoney'

export interface FundProfile {
  thscode: string | null
  ticker: string | null
  fund_name: string | null
  estab_date: string | null
  mgmt_name: string | null
  manager_name: string | null
  fund_type: string | null
  benchmark: string | null
}

export interface EstimateDetail {
  thscode: string
  name: string
  hold_ratio: number
  /** 小数制 */
  change_pct: number
  /** 小数制, 对估算涨跌幅的贡献 */
  contrib_pct: number
}

export interface FundEstimate {
  status: 'ok' | 'insufficient'
  note: string
  /** 小数制 */
  change_pct?: number
  est_nav?: number | null
  prev_nav?: number | null
  covered_weight?: number
  details?: EstimateDetail[]
}

export interface EstimatePoint {
  time: string
  /** 小数制 */
  change_pct: number
  est_nav: number | null
}

export interface FundEstimateCurve {
  status: 'ok' | 'insufficient'
  note: string
  date?: string
  covered_weight?: number
  points: EstimatePoint[]
}

export interface HoldingItem {
  thscode: string
  name: string
  hold_ratio: number
  asset_type: string
}

export interface FeeRule {
  condition: string
  rate_text: string
  discount_text: string | null
}

export interface FundResearch {
  thscode: string
  source: 'eastmoney'
  retrieved_at_ms: number
  horizon: string
  nav_date: string | null
  fees: {
    status: 'ok' | 'partial' | 'unavailable'
    management_pct: number | null
    custody_pct: number | null
    sales_service_pct: number | null
    subscription_rules: FeeRule[]
    redemption_rules: FeeRule[]
    source_url: string
    as_of: null
    note: string
  }
  risk: {
    status: 'ok' | 'unavailable'
    basis: 'source_return_series' | 'unit_nav' | null
    max_drawdown_pct: number | null
    start_date: string | null
    end_date: string | null
    observations: number
    note: string
    source_url: string
  }
  holdings: {
    status: 'ok' | 'unavailable'
    report_date: string | null
    publication_date: null
    items: HoldingItem[]
    coverage_weight_pct: number | null
    report_note: string
    source_url: string
    related_reports: Array<{
      title: string
      publication_date: string
      report_date: string
      source_url: string
    }>
  }
  missing_fields: string[]
  warnings: string[]
}

export type AiFundType = 'all' | '股票型' | '混合型' | '指数型' | '债券型'

export interface FundRankItem {
  code: string
  name: string
  /** Ranking category that supplied this candidate; older saved results may omit it. */
  fund_type?: Exclude<AiFundType, 'all'>
  share_class: string
  nav: number | null
  nav_date: string | null
  purchase_fee_text: string | null
  research?: FundResearch
  growth_1w: number | null
  growth_1m: number | null
  growth_3m: number | null
  growth_6m: number | null
  growth_1y: number | null
  growth_2y: number | null
  growth_3y: number | null
}

export interface AiPickEvent {
  type: 'meta' | 'research' | 'delta' | 'error' | 'done' | 'ping'
  candidates?: FundRankItem[]
  unavailable_types?: Array<Exclude<AiFundType, 'all'>>
  code?: string
  research?: FundResearch
  completed?: number
  total?: number
  source?: string
  data_as_of?: string | null
  retrieved_at_ms?: number
  content?: string
  message?: string
}

const BASE = '/api/custom/fund'

/** 网关层瞬断 (免费隧道闪断) 值得重试的状态码。 */
const RETRYABLE_STATUS = new Set([502, 503, 504])
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

/** 隧道闪断时服务端直接掐连接, 先抛出来由外层判断是否重试。 */
class TransientGatewayError extends Error {
  constructor(readonly status: number) {
    super(`gateway ${status}`)
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET'
  // 只对幂等 GET/HEAD 自动重试, 避免 POST/DELETE 重复提交
  const idempotent = method === 'GET' || method === 'HEAD'
  for (let attempt = 1; ; attempt++) {
    try {
      return await doReq<T>(path, init)
    } catch (err) {
      const transient =
        err instanceof TransientGatewayError ||
        err instanceof TypeError // fetch 裸网络错误: Failed to fetch 等
      if (!idempotent || !transient || attempt >= 3) throw err
      await sleep(700 * attempt)
    }
  }
}

async function doReq<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, { credentials: 'same-origin', ...init })
  if (!res.ok) {
    let detail = ''
    try {
      const parsed = (await res.json()) as { detail?: string }
      detail = parsed.detail ?? ''
    } catch {
      /* 忽略解析错误 */
    }
    // FastAPI 的业务 503 带明确 detail，不能误报为网关瞬断并重复重试。
    if (!detail && RETRYABLE_STATUS.has(res.status)) throw new TransientGatewayError(res.status)
    throw new Error(detail || `基金接口请求失败: ${res.status}`)
  }
  return (await res.json()) as T
}

async function* streamFundEvents<T>(path: string, payload: object, signal?: AbortSignal): AsyncGenerator<T> {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(payload),
    signal,
  })
  if (!res.ok) {
    let detail = ''
    try {
      const body = (await res.json()) as { detail?: string; message?: string }
      detail = body.detail ?? body.message ?? ''
    } catch { /* 网络层可能返回非 JSON */ }
    throw new Error(detail || `基金接口请求失败: ${res.status}`)
  }
  if (!res.body) throw new Error('基金接口未返回数据流')
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let pending = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      pending += done ? decoder.decode() : decoder.decode(value, { stream: true })
      const lines = pending.split('\n')
      pending = lines.pop() ?? ''
      for (const line of lines) {
        if (line.trim()) yield JSON.parse(line) as T
      }
      if (done) break
    }
    if (pending.trim()) yield JSON.parse(pending) as T
  } finally {
    reader.releaseLock()
  }
}

export const fundApi = {
  search(q: string, limit = 50): Promise<{ items: FundSearchItem[] }> {
    return req(`/search?q=${encodeURIComponent(q)}&limit=${limit}`)
  },
  watchlist(): Promise<{ items: FundWatchItem[] }> {
    return req('/watchlist')
  },
  addWatch(item: { thscode: string; name?: string | null; asset_type?: string | null }) {
    return req<{ items: FundWatchItem[] }>('/watchlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(item),
    })
  },
  removeWatch(thscode: string) {
    return req<{ items: FundWatchItem[] }>(`/watchlist/${encodeURIComponent(thscode)}`, {
      method: 'DELETE',
    })
  },
  portfolio(): Promise<{ items: FundPortfolioItem[] }> {
    return req('/portfolio')
  },
  savePortfolio(items: FundPortfolioItem[]) {
    return req<{ items: FundPortfolioItem[] }>('/portfolio', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(items),
    })
  },
  removePosition(thscode: string) {
    return req<{ items: FundPortfolioItem[] }>(`/portfolio/${encodeURIComponent(thscode)}`, {
      method: 'DELETE',
    })
  },
  quote(thscode: string): Promise<{ quote: FundQuote | null }> {
    return req(`/quote/${encodeURIComponent(thscode)}`)
  },
  kline(thscode: string, days = 250): Promise<{ thscode: string; adjusted: string; bars: FundKBar[] }> {
    return req(`/kline/${encodeURIComponent(thscode)}?days=${days}`)
  },
  nav(thscode: string, range = 'year'): Promise<{ thscode: string; nav: FundNavPoint[]; source?: FundDataSource }> {
    return req(`/nav/${encodeURIComponent(thscode)}?range=${range}`)
  },
  research(thscode: string, horizon = '1y'): Promise<FundResearch> {
    return req(`/research/${encodeURIComponent(thscode)}?horizon=${encodeURIComponent(horizon)}`)
  },
  profile(thscode: string): Promise<{ profile: FundProfile | null; source?: FundDataSource }> {
    return req(`/profile/${encodeURIComponent(thscode)}`)
  },
  /** 场外基金当日盘中估值 (穿透自算) */
  estimate(thscode: string): Promise<{
    thscode: string
    prev_nav: number | null
    nav_date: string | null
    estimate: FundEstimate
    curve: FundEstimateCurve
    holdings: {
      stock_ratio_pct: number | null
      items: HoldingItem[]
      skipped: HoldingItem[]
      report_note: string
    }
  }> {
    return req(`/estimate/${encodeURIComponent(thscode)}`)
  },
  /** AI 基金分析 (流式 NDJSON) */
  async *analyzeStream(
    thscode: string,
    focus?: string,
  ): AsyncGenerator<{
    type: 'meta' | 'delta' | 'error' | 'done' | 'ping'
    symbol?: string
    summary?: string
    stats?: Record<string, number | null>
    content?: string
    message?: string
  }> {
    yield* streamFundEvents('/analyze', { thscode, focus: focus ?? '' })
  },
  /** AI 对基金榜单候选进行研究排序，不自动执行交易。 */
  async *aiPickStream(
    options: { fund_type: AiFundType; horizon: string; share: string },
    signal?: AbortSignal,
  ): AsyncGenerator<AiPickEvent> {
    yield* streamFundEvents<AiPickEvent>('/screener/ai', options, signal)
  },
  /** 基金筛选与智能推荐 */
  screener(
    fundType: string = '股票型',
    opts: { share?: string; sortBy?: string; limit?: number; recommend?: boolean } = {},
  ): Promise<{
    fund_type: string
    mode: 'manual' | 'recommend'
    // recommend 模式
    short_term?: Array<{
      code: string
      name: string
      growth_1m: number | null
      growth_3m: number | null
      growth_6m: number | null
      short_score: number
      reason: string
    }>
    long_term?: Array<{
      code: string
      name: string
      growth_1y: number | null
      growth_2y: number | null
      growth_3y: number | null
      long_score: number
      reason: string
    }>
    // manual 模式
    share?: string
    sort_by?: string
    count?: number
    items?: Array<{
      code: string
      name: string
      growth_1w: number | null
      growth_1m: number | null
      growth_3m: number | null
      growth_6m: number | null
      growth_1y: number | null
      growth_2y: number | null
      growth_3y: number | null
      share_class: string
    }>
  }> {
    const p = new URLSearchParams({
      fund_type: fundType,
      share: opts.share ?? 'all',
      sort_by: opts.sortBy ?? '1y',
      limit: String(opts.limit ?? 50),
      recommend: String(!!opts.recommend),
    })
    return req(`/screener?${p}`)
  },
}

/** 小数制 → 百分比文案, null 显示 — */
export function pctText(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return `${(v * 100).toFixed(digits)}%`
}
