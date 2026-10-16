import { useEffect, useMemo, useRef, useState } from 'react'
import { LoaderCircle, RefreshCw } from 'lucide-react'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { chartPrice } from '@/lib/chartPrice'
import { usePageState } from '@/lib/usePageState'

type AuctionPhase = 'opening' | 'closing'
type AuctionState = 'ok' | 'partial' | 'empty' | 'source_unavailable' | 'not_cached' | 'error' | 'busy'

interface AuctionStatus {
  available: boolean
  source: string
  mode: 'on_demand'
}

interface AuctionPoint {
  time: string
  phase: AuctionPhase
  price: number | null
  matched_volume_hand: number | null
  unmatched_volume_hand: number | null
  unmatched_direction: number | string | null
}

interface OfficialMatch {
  time: string
  price: number | null
  volume_hand: number | null
  amount_yuan: number | null
}

interface AuctionEvidence {
  symbol: string
  trade_date: string
  source: string
  state: AuctionState
  observed_at: string | null
  cached: boolean
  stale: boolean
  points: AuctionPoint[]
  opening_match: OfficialMatch | null
  reference_price: number | null
  open_change_pct: number | null
  notes: string[]
  errors: string[]
}

const QUICK_SYMBOLS = [
  { symbol: '511360.SH', name: '短融ETF' },
  { symbol: '518880.SH', name: '黄金ETF' },
  { symbol: '512800.SH', name: '银行ETF' },
  { symbol: '000001.SZ', name: '平安银行' },
] as const

const STATE_LABELS: Record<AuctionState, string> = {
  ok: '已取得竞价数据',
  partial: '数据部分可用',
  empty: '本次未返回竞价记录',
  source_unavailable: '免费数据源暂不可用',
  not_cached: '尚无缓存，可主动拉取',
  error: '数据读取失败',
  busy: '数据源正在处理请求',
}

function chinaToday() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return values.year + '-' + values.month + '-' + values.day
}

function validTradeDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parsed = new Date(value + 'T00:00:00.000Z')
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

function finiteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function displayCount(value: unknown) {
  return finiteNumber(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '—'
}

function displayAmount(value: unknown) {
  return finiteNumber(value)
    ? '¥' + value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
    : '—'
}

function displayPercent(value: unknown) {
  return finiteNumber(value) ? (value > 0 ? '+' : '') + value.toFixed(2) + '%' : '—'
}

function displayObservedAt(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}

function displayDirection(value: AuctionPoint['unmatched_direction']) {
  if (value == null || value === '') return '—'
  return '原始方向 ' + String(value)
}

function auctionTimeMinutes(value: string) {
  const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(value)
  if (!match) return null
  return Number(match[1]) * 60 + Number(match[2]) + Number(match[3] ?? 0) / 60
}

async function readJson<T>(url: string, init: RequestInit, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { ...init, signal, credentials: 'same-origin', cache: 'no-store' })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null
    const detail = typeof body?.detail === 'string' ? '：' + body.detail.slice(0, 160) : ''
    throw new Error('HTTP ' + response.status + detail)
  }
  try {
    return await response.json() as T
  } catch {
    throw new Error('服务器返回的内容不是有效 JSON')
  }
}

function errorText(error: unknown, signal: AbortSignal) {
  if (signal.aborted) return '请求超过 35 秒或已取消，请检查服务状态后手动重试。'
  return error instanceof Error ? error.message : '未知网络错误'
}

type AuctionView = { symbol: string; symbolName: string; tradeDate: string; phase: AuctionPhase }
function isAuctionView(value: unknown): value is AuctionView {
  if (!value || typeof value !== 'object') return false
  const view = value as AuctionView
  return typeof view.symbol === 'string' && /^\d{6}\.(SH|SZ|BJ)$/.test(view.symbol)
    && typeof view.symbolName === 'string' && view.symbolName.length <= 100
    && typeof view.tradeDate === 'string' && validTradeDate(view.tradeDate)
    && (view.phase === 'opening' || view.phase === 'closing')
}

export function FreeAuctionCard() {
  const [view, setView] = usePageState<AuctionView>('auction', () => ({
    symbol: QUICK_SYMBOLS[0].symbol, symbolName: QUICK_SYMBOLS[0].name, tradeDate: chinaToday(), phase: 'opening',
  }), isAuctionView)
  const { symbol, symbolName, tradeDate, phase } = view
  const setPhase = (phase: AuctionPhase) => setView(previous => ({ ...previous, phase }))
  const [status, setStatus] = useState<AuctionStatus | null>(null)
  const [evidence, setEvidence] = useState<AuctionEvidence | null>(null)
  const [page, setPage] = useState(0)
  const [busy, setBusy] = useState<'cache' | 'refresh' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const sequenceRef = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)
  const timeoutRef = useRef<number | null>(null)

  useEffect(() => () => {
    sequenceRef.current += 1
    controllerRef.current?.abort()
    if (timeoutRef.current != null) window.clearTimeout(timeoutRef.current)
  }, [])

  // Restore only the server's existing evidence; fetching source data remains a manual action.
  useEffect(() => {
    void loadCached()
    return () => stopActiveRequest()
    // Context changes must cancel old requests before reading another stock/date.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, tradeDate])

  const matchingEvidence = evidence?.symbol === symbol && evidence.trade_date === tradeDate
    ? evidence
    : null
  const phasePoints = useMemo(
    () => (matchingEvidence?.points ?? [])
      .filter(point => point.phase === phase)
      .slice()
      .sort((left, right) => {
        const leftTime = auctionTimeMinutes(left.time)
        const rightTime = auctionTimeMinutes(right.time)
        if (leftTime == null || rightTime == null) return left.time.localeCompare(right.time)
        return leftTime - rightTime
      }),
    [matchingEvidence, phase],
  )
  const latestFirst = useMemo(() => phasePoints.slice().reverse(), [phasePoints])
  const pageCount = Math.max(1, Math.ceil(latestFirst.length / 20))
  const pagePoints = latestFirst.slice(page * 20, (page + 1) * 20)

  function stopActiveRequest() {
    sequenceRef.current += 1
    controllerRef.current?.abort()
    controllerRef.current = null
    if (timeoutRef.current != null) window.clearTimeout(timeoutRef.current)
    timeoutRef.current = null
    setBusy(null)
  }

  function changeInstrument(nextSymbol: string, nextName: string) {
    stopActiveRequest()
    setView(previous => ({ ...previous, symbol: nextSymbol, symbolName: nextName, phase: 'opening' }))
    setEvidence(null)
    setError(null)
    setPage(0)
  }

  function changeTradeDate(nextDate: string) {
    stopActiveRequest()
    setView(previous => ({ ...previous, tradeDate: nextDate }))
    setEvidence(null)
    setError(null)
    setPage(0)
  }

  function beginRequest(kind: 'cache' | 'refresh') {
    if (!symbol.trim()) {
      setError('请先选择证券。')
      return null
    }
    if (!validTradeDate(tradeDate)) {
      setError('请选择有效的中国交易日期。')
      return null
    }

    controllerRef.current?.abort()
    if (timeoutRef.current != null) window.clearTimeout(timeoutRef.current)
    const controller = new AbortController()
    controllerRef.current = controller
    const requestId = ++sequenceRef.current
    const requestedSymbol = symbol
    const requestedDate = tradeDate
    const timer = window.setTimeout(() => controller.abort(), 35_000)
    timeoutRef.current = timer
    setBusy(kind)
    setError(null)
    setPage(0)
    return {
      controller,
      requestId,
      requestedSymbol,
      requestedDate,
      finish: () => {
        window.clearTimeout(timer)
        if (timeoutRef.current === timer) timeoutRef.current = null
        if (controllerRef.current === controller) controllerRef.current = null
      },
      isCurrent: () => sequenceRef.current === requestId
        && requestedSymbol === symbol
        && requestedDate === tradeDate,
    }
  }

  async function loadCached() {
    const request = beginRequest('cache')
    if (!request) return
    const query = new URLSearchParams({
      symbol: request.requestedSymbol,
      trade_date: request.requestedDate,
    })
    const [statusResult, evidenceResult] = await Promise.allSettled([
      readJson<AuctionStatus>('/api/custom/auction/status', { method: 'GET' }, request.controller.signal),
      readJson<AuctionEvidence>('/api/custom/auction/evidence?' + query.toString(), { method: 'GET' }, request.controller.signal),
    ])
    request.finish()
    if (!request.isCurrent()) return
    setBusy(null)
    if (statusResult.status === 'fulfilled') setStatus(statusResult.value)
    if (evidenceResult.status === 'fulfilled') {
      setEvidence(evidenceResult.value)
      const result = evidenceResult.value
      if (result.symbol !== request.requestedSymbol || result.trade_date !== request.requestedDate) {
        setEvidence(null)
        setError('服务器返回的标的或日期与当前选择不一致，已隐藏该结果。')
      }
    } else {
      setError('读取缓存失败：' + errorText(evidenceResult.reason, request.controller.signal))
    }
    if (statusResult.status === 'rejected' && evidenceResult.status === 'fulfilled') {
      setError('数据源状态读取失败：' + errorText(statusResult.reason, request.controller.signal))
    }
  }

  async function refreshEvidence() {
    const request = beginRequest('refresh')
    if (!request) return
    try {
      const result = await readJson<AuctionEvidence>('/api/custom/auction/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: request.requestedSymbol,
          trade_date: request.requestedDate,
        }),
      }, request.controller.signal)
      if (!request.isCurrent()) return
      if (result.symbol !== request.requestedSymbol || result.trade_date !== request.requestedDate) {
        setEvidence(null)
        setError('服务器返回的标的或日期与当前选择不一致，已隐藏该结果。')
        return
      }
      setEvidence(result)
      setPage(0)
      setError(null)
    } catch (requestError) {
      if (request.isCurrent()) {
        setError('主动拉取失败：' + errorText(requestError, request.controller.signal))
      }
    } finally {
      request.finish()
      if (request.isCurrent()) setBusy(null)
    }
  }

  const chart = useMemo(() => {
    const withPrice = phasePoints.filter(point => finiteNumber(point.price))
    if (withPrice.length === 0) return null

    const prices = withPrice.map(point => point.price as number)
    const minPrice = Math.min(...prices)
    const maxPrice = Math.max(...prices)
    const spread = maxPrice - minPrice
    const pad = spread > 0 ? spread * 0.12 : Math.max(Math.abs(maxPrice) * 0.002, 0.001)
    const low = minPrice - pad
    const high = maxPrice + pad
    const plot = { left: 48, top: 12, width: 500, height: 112 }
    const timeRange = phase === 'opening' ? [9 * 60 + 15, 9 * 60 + 25] : [14 * 60 + 57, 15 * 60]
    const coordinates = withPrice.map((point, index) => {
      const minutes = auctionTimeMinutes(point.time)
      const rawX = minutes == null
        ? index / Math.max(1, withPrice.length - 1)
        : (minutes - timeRange[0]) / (timeRange[1] - timeRange[0])
      const x = plot.left + Math.max(0, Math.min(1, rawX)) * plot.width
      const y = plot.top + (high - (point.price as number)) / (high - low) * plot.height
      return { point, x, y }
    })
    const middlePrice = (low + high) / 2
    const ticks = phase === 'opening'
      ? ['09:15', '09:20', '09:25']
      : ['14:57', '14:58:30', '15:00']
    return { coordinates, low, middlePrice, high, plot, ticks, timeRange }
  }, [phase, phasePoints])

  const state = matchingEvidence?.state
  const displayState = state ? STATE_LABELS[state] : null
  const hasValidDate = validTradeDate(tradeDate)
  const actionDisabled = busy !== null || !symbol || !hasValidDate

  return (
    <section className="rounded-card border border-border bg-surface/80">
      <div className="border-b border-border/60 px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-[13px] font-semibold text-foreground">免费集合竞价查询</h2>
            <p className="mt-1 text-[10px] text-muted">
              股票 / ETF · 按需读取本地缓存或主动拉取 · 日期按中国时区填写
            </p>
          </div>
          {status && (
            <span className={'rounded-full border px-2 py-1 text-[10px] ' + (
              status.available
                ? 'border-bull/25 bg-bull/10 text-bull'
                : 'border-warning/25 bg-warning/10 text-warning'
            )}>
              {status.available ? '免费源已安装' : '免费源未安装'} · {status.source}
            </span>
          )}
        </div>

        <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(260px,1fr)_180px_auto]">
          <div>
            <label className="mb-1 block text-[10px] text-muted">搜索股票或 ETF</label>
            <StockFinancialSearch
              assetTypes="stock,etf"
              placeholder="输入股票 / ETF代码或名称"
              onSelect={(nextSymbol, nextName) => changeInstrument(nextSymbol, nextName)}
            />
          </div>
          <label className="block">
            <span className="mb-1 block text-[10px] text-muted">交易日期（中国时区）</span>
            <input
              type="date"
              value={tradeDate}
              onChange={event => changeTradeDate(event.target.value)}
              className="h-10 w-full rounded-btn border border-border bg-base px-3 text-xs text-foreground focus:border-accent/50 focus:outline-none"
              aria-label="交易日期（中国时区）"
            />
          </label>
          <div className="flex items-end gap-2">
            <button
              type="button"
              onClick={() => void loadCached()}
              disabled={actionDisabled}
              className="inline-flex h-10 items-center gap-1.5 rounded-btn border border-border bg-base px-3 text-xs text-secondary transition-colors hover:border-accent/40 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy === 'cache' ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : null}
              读取缓存
            </button>
            <button
              type="button"
              onClick={() => void refreshEvidence()}
              disabled={actionDisabled}
              className="inline-flex h-10 items-center gap-1.5 rounded-btn bg-accent px-3 text-xs font-medium text-white transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy === 'refresh'
                ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                : <RefreshCw className="h-3.5 w-3.5" />}
              拉取 / 刷新
            </button>
          </div>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-[9px] text-muted">快捷选择</span>
          {QUICK_SYMBOLS.map(item => (
            <button
              key={item.symbol}
              type="button"
              aria-pressed={symbol === item.symbol}
              onClick={() => changeInstrument(item.symbol, item.name)}
              className={'rounded-full border px-2 py-1 text-[10px] transition-colors ' + (
                symbol === item.symbol
                  ? 'border-accent/35 bg-accent/10 text-accent'
                  : 'border-border bg-base/60 text-secondary hover:text-foreground'
              )}
            >
              {item.name} <span className="font-mono text-[9px] opacity-70">{item.symbol}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px]">
          <span className="font-medium text-foreground">{symbolName}</span>
          <span className="font-mono text-muted">{symbol}</span>
          <span className="text-muted">{tradeDate || '未选择日期'}</span>
          {matchingEvidence && displayState && (
            <span className="rounded bg-elevated px-2 py-0.5 text-secondary">{displayState}</span>
          )}
          {matchingEvidence?.cached && (
            <span className="text-muted">缓存读取</span>
          )}
          {matchingEvidence?.stale && (
            <span className="rounded border border-warning/30 bg-warning/10 px-2 py-0.5 text-warning">
              缓存待刷新 · 显示上次观测
            </span>
          )}
          {matchingEvidence?.source && (
            <span className="ml-auto text-muted/80">{matchingEvidence.source}</span>
          )}
        </div>
        {matchingEvidence?.observed_at && (
          <div className="mt-1 text-[9px] text-muted/80">
            观测时间（中国时区）：{displayObservedAt(matchingEvidence.observed_at)}
          </div>
        )}
        {status && !status.available && (
          <p className="mt-2 rounded border border-warning/20 bg-warning/5 px-2.5 py-2 text-[10px] leading-relaxed text-warning">
            当前数据源状态为不可用；竞价卡仍可独立读取已有缓存，点击“拉取 / 刷新”会尝试一次且不会自动重试。
          </p>
        )}
        {error && (
          <p role="alert" className="mt-2 rounded border border-danger/25 bg-danger/10 px-2.5 py-2 text-[10px] leading-relaxed text-danger">
            {error}
          </p>
        )}
        {busy && (
          <p role="status" className="mt-2 text-[10px] text-secondary">
            {busy === 'cache' ? '正在读取状态与本地缓存…' : '正在主动拉取竞价数据…'}
          </p>
        )}

        {matchingEvidence && (
          <>
            {matchingEvidence.stale && (
              <p className="mt-2 rounded border border-warning/20 bg-warning/5 px-2.5 py-2 text-[10px] text-warning">
                这里保留上次观测结果；请使用“拉取 / 刷新”检查是否有更新数据。
              </p>
            )}

            <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              <Metric label="09:25 正式撮合价" value={chartPrice(matchingEvidence.opening_match?.price, symbol)} />
              <Metric label="正式撮合量（手）" value={displayCount(matchingEvidence.opening_match?.volume_hand)} />
              <Metric label="正式撮合金额（元，按价×量计算）" value={displayAmount(matchingEvidence.opening_match?.amount_yuan)} />
              <Metric label="09:25正式成交 / 参考价涨跌" value={displayPercent(matchingEvidence.open_change_pct)} />
            </div>
            <div className="mt-1 text-[9px] text-muted">
              正式撮合 {matchingEvidence.opening_match?.time ?? '09:25'} · 参考价 {chartPrice(matchingEvidence.reference_price, symbol)}
              {' '}· 来源 {matchingEvidence.source || '—'}
            </div>

            <div className="mt-4 rounded-btn border border-border/70 bg-base/40 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="text-[11px] font-medium text-foreground">集合竞价虚拟撮合过程</h3>
                  <p className="mt-0.5 text-[9px] text-warning">
                    虚拟撮合曲线不等于正式成交；09:25 正式撮合结果见上方摘要。
                  </p>
                </div>
                <div className="inline-flex rounded-full border border-border bg-base p-0.5" role="group" aria-label="竞价时段">
                  {([
                    ['opening', '开盘竞价 09:15–09:25'],
                    ['closing', '收盘竞价 14:57–15:00'],
                  ] as const).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={phase === value}
                      onClick={() => { setPhase(value); setPage(0) }}
                      className={'rounded-full px-2.5 py-1 text-[9px] transition-colors ' + (
                        phase === value ? 'bg-accent/15 text-accent' : 'text-muted hover:text-foreground'
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {chart ? (
                <div className="mt-2 overflow-x-auto">
                  <svg
                    viewBox="0 0 590 166"
                    className="h-[166px] min-w-[520px] w-full"
                    role="img"
                    aria-label={phase === 'opening' ? '开盘竞价虚拟撮合价格曲线' : '收盘竞价虚拟撮合价格曲线'}
                  >
                    {[chart.high, chart.middlePrice, chart.low].map((value, index) => {
                      const y = chart.plot.top + index * chart.plot.height / 2
                      return (
                        <g key={index}>
                          <line x1={chart.plot.left} x2={chart.plot.left + chart.plot.width} y1={y} y2={y} stroke="currentColor" className="text-border/60" strokeDasharray={index === 1 ? '3 4' : undefined} />
                          <text x="42" y={y + 3} textAnchor="end" className="fill-muted" fontSize="9">{chartPrice(value, symbol)}</text>
                        </g>
                      )
                    })}
                    {chart.ticks.map((tick, index) => {
                      const x = chart.plot.left + chart.plot.width * index / (chart.ticks.length - 1)
                      return (
                        <g key={tick}>
                          <line x1={x} x2={x} y1={chart.plot.top} y2={chart.plot.top + chart.plot.height} stroke="currentColor" className="text-border/30" />
                          <text x={x} y="148" textAnchor={index === 0 ? 'start' : index === chart.ticks.length - 1 ? 'end' : 'middle'} className="fill-muted" fontSize="9">{tick}</text>
                        </g>
                      )
                    })}
                    {chart.coordinates.length > 1 && (
                      <polyline
                        points={chart.coordinates.map(point => point.x + ',' + point.y).join(' ')}
                        fill="none"
                        stroke="currentColor"
                        className="text-accent"
                        strokeWidth="2"
                        strokeLinejoin="round"
                        strokeLinecap="round"
                      />
                    )}
                    {chart.coordinates.map((item, index) => (
                      <circle
                        key={item.point.time + '-' + index}
                        cx={item.x}
                        cy={item.y}
                        r={chart.coordinates.length > 80 ? 1.8 : 2.8}
                        fill="currentColor"
                        className="text-accent"
                      >
                        <title>
                          {item.point.time} · 虚拟撮合价 {chartPrice(item.point.price, symbol)} · 撮合量 {displayCount(item.point.matched_volume_hand)} 手 · 未匹配量 {displayCount(item.point.unmatched_volume_hand)} 手（{displayDirection(item.point.unmatched_direction)}）
                        </title>
                      </circle>
                    ))}
                  </svg>
                </div>
              ) : (
                <p className="mt-4 rounded border border-border/70 bg-surface/40 px-3 py-5 text-center text-[10px] text-muted">
                  {phase === 'opening' ? '没有可绘制的开盘竞价价格点。' : '该日期没有可绘制的收盘集合竞价数据。'}
                </p>
              )}

              <div className="mt-1 flex items-center justify-between border-t border-border/50 pt-2 text-[9px] text-muted">
                <span>{phase === 'opening' ? '开盘时段 09:15–09:25' : '收盘时段 14:57–15:00'}</span>
                <span>{phasePoints.length} 条过程记录 · 下表每页最多 20 条</span>
              </div>
              {pagePoints.length > 0 ? (
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full min-w-[560px] text-left text-[10px]">
                    <thead>
                      <tr className="border-b border-border/60 text-muted">
                        <th className="px-2 py-1.5 font-medium">时间</th>
                        <th className="px-2 py-1.5 text-right font-medium">虚拟价格</th>
                        <th className="px-2 py-1.5 text-right font-medium">撮合量（手）</th>
                        <th className="px-2 py-1.5 text-right font-medium">未匹配量（手）</th>
                        <th className="px-2 py-1.5 text-right font-medium">未匹配方向（原始）</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pagePoints.map((point, index) => (
                        <tr key={point.time + '-' + index} className="border-b border-border/30 text-secondary last:border-0">
                          <td className="px-2 py-1.5 font-mono tabular-nums">{point.time || '—'}</td>
                          <td className="px-2 py-1.5 text-right font-mono tabular-nums text-foreground">{chartPrice(point.price, symbol)}</td>
                          <td className="px-2 py-1.5 text-right font-mono tabular-nums">{displayCount(point.matched_volume_hand)}</td>
                          <td className="px-2 py-1.5 text-right font-mono tabular-nums">{displayCount(point.unmatched_volume_hand)}</td>
                          <td className="px-2 py-1.5 text-right">{displayDirection(point.unmatched_direction)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {pageCount > 1 && (
                    <div className="flex items-center justify-end gap-2 pt-2 text-[9px] text-muted">
                      <button type="button" disabled={page >= pageCount - 1} onClick={() => setPage(value => Math.min(pageCount - 1, value + 1))} className="rounded border border-border px-2 py-1 hover:text-foreground disabled:opacity-40">更早</button>
                      <span>{page + 1} / {pageCount}</span>
                      <button type="button" disabled={page <= 0} onClick={() => setPage(value => Math.max(0, value - 1))} className="rounded border border-border px-2 py-1 hover:text-foreground disabled:opacity-40">更新</button>
                    </div>
                  )}
                </div>
              ) : (
                <p className="mt-2 border-t border-border/50 px-2 py-3 text-center text-[10px] text-muted">此时段没有过程记录。</p>
              )}
            </div>

            {matchingEvidence.notes.length > 0 && (
              <ul className="mt-3 space-y-1 text-[9px] text-muted">
                {matchingEvidence.notes.map((note, index) => <li key={index}>说明：{note}</li>)}
              </ul>
            )}
            {matchingEvidence.errors.length > 0 && (
              <ul className="mt-2 space-y-1 text-[9px] text-danger">
                {matchingEvidence.errors.map((item, index) => <li key={index}>数据错误：{item}</li>)}
              </ul>
            )}
          </>
        )}
      </div>
    </section>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-btn border border-border/70 bg-base/45 px-3 py-2">
      <div className="text-[9px] text-muted">{label}</div>
      <div className="mt-1 font-mono text-[13px] font-medium tabular-nums text-foreground">{value}</div>
    </div>
  )
}
