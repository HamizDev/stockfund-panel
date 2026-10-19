import { useEffect, useMemo, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CanvasMarketChart, type CanvasChartBar } from '@/components/CanvasMarketChart'
import { EChartsIntraday } from '@/components/EChartsIntraday'
import { api, type IndexMinuteRow, type MinuteKlineRow } from '@/lib/api'
import { chartPrice } from '@/lib/chartPrice'
import { klineMinuteQueryOptions, minuteRefetchInterval } from '@/lib/kline'
import { QK } from '@/lib/queryKeys'
import { INSTRUMENT_VIEW_GC_MS } from '@/lib/useInstrumentViewCache'

type AssetType = 'stock' | 'etf' | 'index' | undefined
type View = 'intraday' | 'candles'

interface Props {
  symbol: string
  assetType: AssetType
  date: string | null
  onDateChange: (date: string | null) => void
  view: View
  onViewChange: (view: View) => void
  onPriceHover?: (price: number | null) => void
  height?: number
}

type StockMinuteResponse = Awaited<ReturnType<typeof api.klineMinute>>
type IndexMinuteResponse = Awaited<ReturnType<typeof api.indexMinute>>
type MinuteResponse = StockMinuteResponse | IndexMinuteResponse

const REFRESH_MS = 30_000
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const TIME_RE = /^(?:[01]\d|2[0-3]):[0-5]\d$/

function matchesContext<T extends MinuteResponse>(response: T, symbol: string, requestedDate: string | null): T {
  if (response.symbol !== symbol) throw new Error('Minute response symbol does not match the selection')
  if (requestedDate && response.date !== requestedDate) throw new Error('Minute response date does not match the selection')
  if (response.date != null && !DATE_RE.test(response.date)) throw new Error('Minute response date is invalid')
  if (requestedDate == null && response.date == null && response.rows.length > 0) {
    throw new Error('Latest minute response is missing its actual date')
  }
  if (response.rows.some(row => {
    const datetime = String(row.datetime ?? '')
    return datetime.length < 16
      || !DATE_RE.test(datetime.slice(0, 10))
      || !TIME_RE.test(datetime.slice(11, 16))
      || (response.date != null && datetime.slice(0, 10) !== response.date)
  })) throw new Error('Minute response contains a row outside its actual date')
  return response
}

function validOhlc(row: MinuteKlineRow): boolean {
  const values = [row.open, row.high, row.low, row.close]
  if (!values.every(value => typeof value === 'number' && Number.isFinite(value) && value > 0)) return false
  const open = row.open!
  return row.high >= Math.max(open, row.close)
    && row.low <= Math.min(open, row.close)
    && row.high >= row.low
}

function sourceLabel(source: string | undefined): string {
  if (source === 'live') return '在线查询'
  if (source === 'local') return '本地'
  if (source === 'none') return '无来源'
  return source || '未知'
}

function errorPanel(hasData: boolean, onRetry: () => void) {
  return <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
    <span>{hasData ? '分钟行情更新失败，仍显示上次有效数据。' : '分钟行情加载失败，请重试。'}</span>
    {!hasData && <button type="button" onClick={onRetry} className="rounded border border-red-400/40 px-2.5 py-1 text-red-200 hover:bg-red-500/10">重试</button>}
  </div>
}

export function AnalysisMinutePanel({
  symbol,
  assetType,
  date,
  onDateChange,
  view,
  onViewChange,
  onPriceHover,
  height = 480,
}: Props) {
  const queryClient = useQueryClient()
  const stockOptions = klineMinuteQueryOptions(symbol, date ?? undefined, true)
  const stockQuery = useQuery({
    ...stockOptions,
    queryKey: QK.analysisMinute(symbol, date ?? '', 'stock'),
    queryFn: async () => matchesContext(await stockOptions.queryFn(), symbol, date),
    enabled: !!symbol && (assetType === 'stock' || assetType === 'etf'),
    retry: false,
    staleTime: REFRESH_MS,
    gcTime: INSTRUMENT_VIEW_GC_MS,
    meta: { instrumentView: 'analysis', symbol, minuteDate: date ?? '' },
    refetchInterval: minuteRefetchInterval(REFRESH_MS),
  })
  const indexQuery = useQuery({
    queryKey: QK.analysisMinute(symbol, date ?? '', 'index'),
    queryFn: async () => matchesContext(await api.indexMinute(symbol, date ?? undefined), symbol, date),
    enabled: !!symbol && assetType === 'index',
    retry: false,
    staleTime: REFRESH_MS,
    gcTime: INSTRUMENT_VIEW_GC_MS,
    meta: { instrumentView: 'analysis', symbol, minuteDate: date ?? '' },
    refetchInterval: minuteRefetchInterval(REFRESH_MS),
  })

  const isIndex = assetType === 'index'
  const activeQuery = isIndex ? indexQuery : stockQuery
  const response = isIndex ? indexQuery.data : stockQuery.data
  const currentResponse = response
    && response.symbol === symbol
    && (date == null ? response.date == null ? response.rows.length === 0 : true : response.date === date)
    ? response
    : undefined
  const rows = currentResponse?.rows ?? []
  const hoverRef = useRef(onPriceHover)
  hoverRef.current = onPriceHover
  useEffect(() => {
    hoverRef.current?.(null)
    return () => hoverRef.current?.(null)
  }, [symbol, date, view, currentResponse?.date])
  const provider = isIndex && currentResponse && 'provider' in currentResponse
    ? currentResponse.provider
    : undefined
  const invalidCandles = !isIndex && rows.length > 0
    && rows.some(row => !validOhlc(row as MinuteKlineRow))
  const candleBars = useMemo<CanvasChartBar[] | null>(() => {
    if (isIndex || rows.length === 0) return []
    const minuteRows = rows as MinuteKlineRow[]
    if (minuteRows.some(row => !validOhlc(row))) return null
    return minuteRows.map(row => ({
      // Minute datetimes are Beijing wall-clock strings. Keep their display label
      // directly instead of parsing through the browser's local timezone.
      time: row.datetime.slice(11, 16),
      open: row.open!,
      high: row.high,
      low: row.low,
      close: row.close,
      volume: typeof row.volume === 'number' && Number.isFinite(row.volume) && row.volume >= 0
        ? row.volume
        : null,
    }))
  }, [isIndex, rows])

  const retry = () => { void activeQuery.refetch() }
  const selectLatest = () => {
    if (!assetType) return
    if (date == null) retry()
    else {
      // Invalidate the destination before switching keys. Refetching the current
      // observer here would request the historical date again.
      void queryClient.invalidateQueries({
        queryKey: QK.analysisMinute(symbol, '', isIndex ? 'index' : 'stock'),
        exact: true,
        refetchType: 'none',
      })
    }
    onDateChange(null)
  }
  const error = activeQuery.isError
  const hasData = !!currentResponse
  const showCandles = view === 'candles' && !isIndex
  const latestTime = rows.length ? String(rows[rows.length - 1].datetime).slice(11, 16) : '—'
  const reference = !isIndex ? currentResponse as StockMinuteResponse | undefined : undefined

  return <section aria-label="日内行情" className="w-full min-w-0 space-y-3 overflow-hidden rounded-lg border border-border/50 bg-surface/40 p-3 sm:p-4">
    <h2 className="text-sm font-medium text-foreground">分时 / 分钟 K</h2>
    <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-xs text-secondary">
          <span>日期</span>
          <input
            type="date"
            aria-label="分钟数据日期"
            value={date ?? currentResponse?.date ?? ''}
            onChange={event => onDateChange(event.target.value || null)}
            className="min-w-0 max-w-40 rounded border border-border/60 bg-base px-2 py-1 text-xs text-foreground"
          />
        </label>
        <button type="button" onClick={selectLatest} aria-pressed={date == null}
          disabled={!assetType || (date == null && activeQuery.isFetching)}
          title="重新查询最新交易日；休市时数据日期可能保持不变"
          className={`shrink-0 rounded border px-2.5 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-40 ${date == null ? 'border-blue-400/50 bg-blue-500/10 text-blue-200' : 'border-border/60 text-secondary hover:text-foreground'}`}>最新交易日</button>
        <button
          type="button"
          onClick={retry}
          disabled={!assetType || activeQuery.isFetching}
          aria-label="刷新分钟数据"
          className="shrink-0 rounded border border-border/60 px-2.5 py-1 text-xs text-secondary hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
        >{activeQuery.isFetching ? '刷新中…' : '刷新'}</button>
      </div>
      <div className="flex shrink-0 items-center gap-1 self-start sm:self-auto" role="group" aria-label="分钟图表类型">
        <button type="button" aria-pressed={view === 'intraday' || isIndex} onClick={() => onViewChange('intraday')}
          className={`rounded border px-2.5 py-1 text-xs ${view === 'intraday' || isIndex ? 'border-blue-400/50 bg-blue-500/10 text-blue-200' : 'border-border/60 text-secondary hover:text-foreground'}`}>分时</button>
        <button type="button" aria-pressed={showCandles} disabled={isIndex}
          onClick={() => onViewChange('candles')}
          title={isIndex ? '指数分钟数据只有价格点位' : undefined}
          className={`rounded border px-2.5 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-40 ${showCandles ? 'border-blue-400/50 bg-blue-500/10 text-blue-200' : 'border-border/60 text-secondary hover:text-foreground'}`}>1分钟K</button>
      </div>
    </div>

    {assetType == null ? <div role="status" className="rounded-md border border-border/50 bg-base/40 px-3 py-4 text-sm text-secondary">资产类型待识别，识别后加载分钟行情。</div> : null}
    {assetType != null && activeQuery.isPending ? <div role="status" className="rounded-md border border-border/50 bg-base/40 px-3 py-4 text-sm text-secondary">正在加载分钟行情…</div> : null}
    {assetType != null && error ? errorPanel(hasData, retry) : null}

    {assetType != null && hasData && currentResponse ? <>
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted" aria-label="分钟数据状态">
        <span className="text-blue-300">{date == null ? '最新交易日模式' : '历史日期模式'}</span>
        <span>来源：{sourceLabel(currentResponse.source)}{provider && provider !== currentResponse.source ? ` · ${provider}` : ''}</span>
        <span>实际日期：{currentResponse.date ?? '—'}</span>
        <span>最后分钟：{latestTime}</span>
        {reference?.prev_close != null && <span>昨收基准：{chartPrice(reference.prev_close, symbol)}{reference.prev_close_date ? ` · ${reference.prev_close_date}` : ''}</span>}
        {activeQuery.isFetching && <span className="text-blue-300">刷新中</span>}
      </div>
      {reference?.prev_close_warning && rows.length > 0 && <p role="status" className="text-[11px] text-amber-300">{reference.prev_close_warning}</p>}
      {rows.length === 0 ? <div role="status" className="flex items-center justify-center rounded-md border border-border/40 bg-base/30 text-sm text-muted" style={{ height }}>暂无分钟数据</div>
        : showCandles && invalidCandles ? <div role="status" className="flex flex-col items-center justify-center gap-1 rounded-md border border-amber-500/30 bg-amber-500/5 px-4 text-center text-sm text-amber-200" style={{ height }}>
          <span>缺少有效的真实分钟 OHLC，无法绘制 1 分钟 K。</span>
          <span className="text-xs text-amber-200/70">请切回分时查看；不会用收盘价补开盘价。</span>
        </div>
          : showCandles && candleBars ? <CanvasMarketChart
            bars={candleBars}
            symbol={symbol}
            title="1分钟K"
            contextKey={`${symbol}:${currentResponse.date ?? 'latest'}`}
            height={height}
            visibleBars="all"
            formatPrice={value => chartPrice(value, symbol)}
            formatTime={time => String(time)}
            onPriceHover={onPriceHover}
          />
            : isIndex ? <EChartsIntraday
              data={currentResponse.rows as IndexMinuteRow[]}
              height={height}
              date={currentResponse.date ?? undefined}
              pricePointsOnly
              onPriceHover={onPriceHover}
            />
              : <EChartsIntraday
                data={currentResponse.rows as MinuteKlineRow[]}
                height={height}
                prevClose={(currentResponse as StockMinuteResponse).prev_close ?? undefined}
                priceLimit={(currentResponse as StockMinuteResponse).price_limit ?? undefined}
                date={currentResponse.date ?? undefined}
                onPriceHover={onPriceHover}
              />}
    </> : null}
    {assetType != null && !activeQuery.isPending && !error && !hasData ? <div role="status" className="flex items-center justify-center rounded-md border border-border/40 bg-base/30 text-sm text-muted" style={{ height }}>暂无分钟数据</div> : null}
  </section>
}
