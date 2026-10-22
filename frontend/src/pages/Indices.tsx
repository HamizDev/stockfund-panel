import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Loader2, RefreshCw } from 'lucide-react'
import { api, type IndexInstrument, type IndexMinuteRow } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cnToday, marketSessionRefetchInterval, minuteRefetchInterval } from '@/lib/kline'
import { usePageState } from '@/lib/usePageState'
import { dailyChartRows } from '@/lib/dailyChartRows'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import { EChartsIntraday } from '@/components/EChartsIntraday'
import { INSTRUMENT_VIEW_GC_MS, useInstrumentViewCache } from '@/lib/useInstrumentViewCache'
import { useIndexQuotes } from '@/lib/useSharedQueries'

function defaultRange() {
  const end = cnToday()
  const [year, month, day] = end.split('-').map(Number)
  const s = new Date(Date.UTC(year, month - 1, day))
  s.setUTCMonth(s.getUTCMonth() - 6)
  return { start: s.toISOString().slice(0, 10), end }
}

function fmtPct(v: number | null | undefined) {
  if (v == null || Number.isNaN(Number(v))) return '--'
  return `${Number(v).toFixed(2)}%`
}

function fmtNum(v: number | null | undefined, digits = 2) {
  if (v == null || Number.isNaN(Number(v))) return '--'
  return Number(v).toFixed(digits)
}

const PINNED_INDEXES = [
  { symbol: '000001.SH', name: '上证指数' },
  { symbol: '399001.SZ', name: '深证成指' },
  { symbol: '399006.SZ', name: '创业板指' },
  { symbol: '000680.SH', name: '科创综指' },
  { symbol: '000688.SH', name: '科创50' },
]

type IndexView = {
  selected: string
  range: { start: string; end: string }
  selectedDate: string | null
  /** Missing only in view state saved by older versions. */
  followLatest?: boolean
  /** Whether the daily query range end follows the Beijing calendar date. */
  rangeEndFollowsToday?: boolean
}
function isIndexView(value: unknown): value is IndexView {
  if (!value || typeof value !== 'object') return false
  const view = value as IndexView
  const date = (v: unknown) => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)
    && Number.isFinite(Date.parse(v))
  return PINNED_INDEXES.some(index => index.symbol === view.selected)
    && !!view.range && date(view.range.start) && date(view.range.end) && view.range.start <= view.range.end
    && (view.selectedDate === null || date(view.selectedDate))
    && (view.followLatest === undefined || typeof view.followLatest === 'boolean')
    && (view.rangeEndFollowsToday === undefined || typeof view.rangeEndFollowsToday === 'boolean')
}

const INDEX_REFRESH_MS = 30_000

export function Indices() {
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const symbolParam = searchParams.get('symbol') ?? ''
  const validSymbolParam = PINNED_INDEXES.some(index => index.symbol === symbolParam) ? symbolParam : ''
  const [view, setView] = usePageState<IndexView>('indices', () => ({
    selected: PINNED_INDEXES[0].symbol, range: defaultRange(), selectedDate: null, followLatest: true,
    rangeEndFollowsToday: true,
  }), isIndexView)
  const { selected, range } = view
  const storedSelectedDate = validSymbolParam && validSymbolParam !== selected ? null : view.selectedDate
  const wantsFollowLatest = view.followLatest ?? storedSelectedDate == null
  const rangeEndFollowsToday = view.rangeEndFollowsToday ?? wantsFollowLatest
  const [beijingToday, setBeijingToday] = useState(cnToday)
  useEffect(() => {
    // Advance the query date during the next market session. This changes the daily
    // query key before fetching, so new-day rows are never cached under yesterday's key.
    let timer: number
    const checkDate = () => {
      const actualToday = cnToday()
      if (actualToday !== beijingToday) {
        setBeijingToday(actualToday)
        return
      }
      const delay = marketSessionRefetchInterval(INDEX_REFRESH_MS)(null) as number
      timer = window.setTimeout(checkDate, delay)
    }
    const delay = marketSessionRefetchInterval(INDEX_REFRESH_MS)(null) as number
    timer = window.setTimeout(checkDate, delay)
    return () => window.clearTimeout(timer)
  }, [beijingToday])
  const dailyEnd = rangeEndFollowsToday ? beijingToday : range.end
  const setSelected = (symbol: string) => setView(previous => ({
    ...previous, selected: symbol, selectedDate: null, followLatest: true,
  }))
  const setRange = (update: (previous: IndexView['range']) => IndexView['range']) => setView(previous => ({ ...previous, range: update(previous.range) }))
  const setSelectedDate = (date: string | null) => setView(previous => ({
    ...previous,
    selectedDate: date,
    followLatest: date == null,
  }))
  const [linkedPrice, setLinkedPrice] = useState<number | null>(null)

  // 指数标的固定核心五只 (产品契约, 不再提供全指数搜索/浏览)
  const topRows: IndexInstrument[] = PINNED_INDEXES.map(p => ({
    symbol: p.symbol, name: p.name, asset_type: 'index' as const,
  }))

  const selectedSymbol = validSymbolParam || selected
  useInstrumentViewCache('indices', selectedSymbol)
  const previousSymbol = useRef(selectedSymbol)

  useEffect(() => {
    if (validSymbolParam && validSymbolParam !== selected) setSelected(validSymbolParam)
  }, [selected, validSymbolParam])

  const selectIndex = (symbol: string) => {
    setSelected(symbol)
    setSearchParams({ symbol })
  }

  const quotes = useIndexQuotes()

  const daily = useQuery({
    queryKey: QK.indexDaily(selectedSymbol, range.start, dailyEnd),
    queryFn: () => api.indexDaily(selectedSymbol, 180, { start: range.start, end: dailyEnd }),
    meta: { instrumentView: 'indices', symbol: selectedSymbol },
    gcTime: INSTRUMENT_VIEW_GC_MS,
    enabled: !!selectedSymbol,
    refetchInterval: wantsFollowLatest ? marketSessionRefetchInterval(INDEX_REFRESH_MS) : false,
  })

  const chartRows = useMemo(() => dailyChartRows(daily.data?.symbol === selectedSymbol ? daily.data.rows : []), [daily.data?.rows, daily.data?.symbol, selectedSymbol])
  const latestDailyDate = chartRows.at(-1)?.date ?? null
  const savedDateIsValid = storedSelectedDate == null || chartRows.some(row => row.date === storedSelectedDate)
  // Older saved state has no mode bit. A saved date matching the latest available
  // candle came from automatic selection; preserve only dates that were actually historical.
  const followsLatest = view.followLatest ?? (storedSelectedDate == null || !savedDateIsValid || storedSelectedDate === latestDailyDate)
  const selectedDate = followsLatest ? latestDailyDate : storedSelectedDate

  const minute = useQuery({
    queryKey: QK.indexMinute(selectedSymbol, selectedDate ?? ''),
    queryFn: () => api.indexMinute(selectedSymbol, selectedDate!),
    enabled: !!selectedSymbol && !!selectedDate && daily.data?.symbol === selectedSymbol
      && daily.data.rows.some(row => row.date === selectedDate),
    retry: false,
    refetchInterval: minuteRefetchInterval(INDEX_REFRESH_MS),
  })

  const syncDaily = useMutation({
    mutationFn: () => api.syncIndexDaily(365),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.indexQuotes })
      qc.invalidateQueries({ queryKey: ['index-daily'] })
    },
  })

  const quoteBySymbol = useMemo(() => {
    const m = new Map<string, any>()
    for (const q of quotes.data?.rows ?? []) m.set(q.symbol, q)
    return m
  }, [quotes.data?.rows])
  const selectedQuote = selectedSymbol ? quoteBySymbol.get(selectedSymbol) : null
  const selectedQuoteValue = selectedQuote?.last_price ?? selectedQuote?.price ?? selectedQuote?.close
  const selectedQuotePct = selectedQuote?.change_pct ?? selectedQuote?.pct

  const selectedInfo = topRows.find(r => r.symbol === selectedSymbol) || daily.data?.index_info
  const currentMinute = minute.data?.symbol === selectedSymbol && minute.data?.date === selectedDate
    && daily.data?.symbol === selectedSymbol ? minute.data : undefined
  const minuteRows: IndexMinuteRow[] = currentMinute?.rows ?? []
  const selectedIdx = selectedDate ? chartRows.findIndex(r => r.date === selectedDate) : -1
  const prevClose = selectedIdx > 0 ? chartRows[selectedIdx - 1].close : undefined

  const minuteSource = currentMinute?.source === 'live' ? '在线查询'
    : currentMinute?.source === 'none' ? '暂无有效数据'
      : currentMinute?.source ?? '--'
  const minuteProvider = currentMinute?.provider === 'txquote' ? '腾讯免费分时' : currentMinute?.provider

  useEffect(() => {
    if (previousSymbol.current !== selectedSymbol) {
      setView(previous => ({ ...previous, selectedDate: null, followLatest: true }))
    }
    previousSymbol.current = selectedSymbol
    setLinkedPrice(null)
  }, [selectedSymbol, setView])

  useEffect(() => {
    if (chartRows.length === 0 || daily.data?.symbol !== selectedSymbol) return
    if (view.followLatest === undefined) {
      setView(previous => previous.followLatest === undefined
        ? { ...previous, followLatest: followsLatest, selectedDate: followsLatest ? null : previous.selectedDate }
        : previous)
      return
    }
    if (!view.followLatest && storedSelectedDate && !savedDateIsValid) {
      setView(previous => previous.followLatest === false
        ? { ...previous, selectedDate: latestDailyDate }
        : previous)
    }
  }, [chartRows.length, daily.data?.symbol, followsLatest, latestDailyDate, savedDateIsValid, selectedSymbol, setView, storedSelectedDate, view.followLatest])
  const renderIndexItem = (item: IndexInstrument) => {
    const q = quoteBySymbol.get(item.symbol)
    const pct = q?.change_pct ?? q?.pct
    const current = q?.last_price ?? q?.price ?? q?.close
    const active = item.symbol === selectedSymbol
    return (
      <button
        key={item.symbol}
        onClick={() => selectIndex(item.symbol)}
        className={`w-full rounded-btn px-2 py-2 text-left transition-colors ${active ? 'bg-accent/15 text-foreground' : 'hover:bg-elevated text-secondary'}`}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-xs font-medium">{item.name || item.symbol}</span>
          <span className={`text-[10px] font-mono ${Number(pct ?? 0) >= 0 ? 'text-bull' : 'text-bear'}`}>{fmtPct(pct)}</span>
        </div>
        <div className="mt-0.5 flex items-center justify-between text-[10px] font-mono text-muted">
          <span>{item.symbol}</span>
          <span>{fmtNum(current)}</span>
        </div>
      </button>
    )
  }

  return (
    <div className="h-full overflow-auto bg-base p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 pl-10 md:pl-0">
        <div>
          <h1 className="text-lg font-semibold text-foreground">指数</h1>
          <p className="mt-1 text-xs text-muted">
            核心指数走势 · 日K与分时 · 数据日期以图表标注为准
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => syncDaily.mutate()}
            disabled={syncDaily.isPending}
            className="inline-flex items-center gap-1.5 rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90 disabled:opacity-50"
          >
            {syncDaily.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            同步指数日K
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[15rem_minmax(0,1fr)]">
        <aside className="rounded-card border border-border bg-surface p-3">
          <div className="mb-2 px-1 text-[11px] uppercase tracking-wider text-muted">核心指数</div>
          <div className="space-y-1">
            {topRows.map(renderIndexItem)}
          </div>
        </aside>

        <main className="min-w-0 rounded-card border border-border bg-surface p-3">
          <div className="mb-3 flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Activity className="h-4 w-4 text-accent" />
                <h2 className="truncate text-sm font-semibold text-foreground">
                  {selectedInfo?.name || selectedSymbol || '未选择指数'}
                </h2>
                {selectedSymbol && <span className="font-mono text-xs text-muted">{selectedSymbol}</span>}
                {selectedSymbol && <span className="font-mono text-xs text-foreground">{fmtNum(selectedQuoteValue)}</span>}
                {selectedSymbol && <span className={`font-mono text-xs ${Number(selectedQuotePct ?? 0) >= 0 ? 'text-bull' : 'text-bear'}`}>{fmtPct(selectedQuotePct)}</span>}
              </div>
              <div className="mt-1 text-xs text-muted">
                已缓存 {quotes.data?.count ?? 0} 只指数 · 日K截至 {chartRows.at(-1)?.date ?? '--'}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <input
                type="date"
                value={range.start}
                onChange={e => setRange(r => ({ ...r, start: e.target.value }))}
                className="rounded-btn border border-border bg-base px-2 py-1 text-secondary outline-none focus:border-accent"
              />
              <span className="text-muted">至</span>
              <input
                type="date"
                value={dailyEnd}
                onChange={e => setView(previous => ({
                  ...previous,
                  range: { ...previous.range, end: e.target.value },
                  rangeEndFollowsToday: false,
                }))}
                className="rounded-btn border border-border bg-base px-2 py-1 text-secondary outline-none focus:border-accent"
              />
            </div>
          </div>

          {daily.isLoading && <div className="py-10 text-center text-sm text-muted">日K加载中…</div>}
          {daily.isError && <div className="py-4 text-sm text-danger">指数日K加载失败</div>}
          {!daily.isLoading && !daily.isError && chartRows.length === 0 && (
            <div className="rounded-card bg-elevated p-6 text-center text-sm text-muted">
              暂无日K数据。可以先同步指数日K，或选择其他指数。
            </div>
          )}
          {chartRows.length > 0 && (
            <div className="grid min-w-0 grid-cols-1 gap-4 2xl:grid-cols-2">
              <div className="min-w-0">
                <EChartsCandlestick
                  data={chartRows}
                  height={620}
                  showMA={true}
                  showInfoBar={true}
                  showMarkers={false}
                  symbol={selectedSymbol}
                  linkedPrice={linkedPrice}
                  onDateClick={(date) => {
                    setSelectedDate(date)
                    setLinkedPrice(null)
                  }}
                  visibleBars={48}
                  activeIndicators={['vol', 'macd']}
                />
              </div>
              <div className="min-w-0 border-t border-border pt-3 2xl:border-l 2xl:border-t-0 2xl:pl-3 2xl:pt-0" style={{ minHeight: 620 }}>
                <div className="mb-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[10px] text-muted">
                  <span>
                    日期 {currentMinute?.date ?? selectedDate ?? '--'}
                    {' · '}{followsLatest ? '跟随最新' : '手选历史'}
                    {' · '}{currentMinute?.data_kind === 'price_points' ? '价格点位' : '分时'}
                  </span>
                  <button
                    type="button"
                    onClick={() => setView(previous => ({
                      ...previous, selectedDate: null, followLatest: true, rangeEndFollowsToday: true,
                    }))}
                    disabled={followsLatest || !latestDailyDate}
                    aria-pressed={followsLatest}
                    className="rounded border border-border/60 px-2 py-0.5 text-muted hover:text-foreground disabled:cursor-default disabled:opacity-60"
                  >{followsLatest ? '跟随最新' : '回到最新'}</button>
                  <span>
                    来源 {minuteSource}{minuteProvider ? ` · ${minuteProvider}` : ''}
                    {' · '}{currentMinute?.history_days != null
                      ? `近${currentMinute.history_days}个交易日，以来源实际覆盖为准`
                      : '历史范围 --'}
                  </span>
                </div>
                {minute.isPending && <div className="flex h-[580px] items-center justify-center text-xs text-muted">指数分时加载中…</div>}
                {minute.isError && (
                  <div role="alert" className="flex min-h-[120px] flex-col items-center justify-center gap-2 text-center">
                    <div className="text-xs text-danger">{selectedDate ?? '所选日期'} 指数分时加载失败</div>
                    <button
                      onClick={() => { void minute.refetch() }}
                      disabled={minute.isFetching}
                      className="rounded-btn border border-border px-2.5 py-1 text-xs text-secondary hover:bg-elevated disabled:opacity-50"
                    >
                      {minute.isFetching ? '重试中…' : '重试'}
                    </button>
                  </div>
                )}
                {!minute.isPending && !minute.isError && minuteRows.length === 0 && (
                  <div className="flex min-h-[120px] items-center justify-center text-xs text-muted">
                    {selectedDate ?? '所选日期'} 暂无指数分时数据
                  </div>
                )}
                {minuteRows.length > 0 && (
                  <EChartsIntraday
                    key={`${selectedSymbol}:${selectedDate}`}
                    data={minuteRows}
                    height={620}
                    prevClose={prevClose}
                    date={selectedDate ?? undefined}
                    showLimitLines={false}
                    showAvgLine={false}
                    pricePointsOnly={true}
                    onPriceHover={setLinkedPrice}
                  />
                )}
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
