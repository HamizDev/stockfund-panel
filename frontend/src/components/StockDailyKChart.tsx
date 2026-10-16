import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { type KlineRow } from '@/lib/api'
import { klineDailyQueryOptions } from '@/lib/kline'
import { storage } from '@/lib/storage'
import { dailyChartRows } from '@/lib/dailyChartRows'
import {
  EChartsCandlestick,
  OVERLAY_INDICATORS,
  SUB_CHARTS,
  type ChartMarker,
  type ChartPriceLine,
  type ChartRange,
  type VolumeCompareConfig,
} from '@/components/EChartsCandlestick'
import { TradingViewDailyChart } from '@/components/TradingViewDailyChart'
import { ChartResearchPanel } from '@/components/research/ChartResearchPanel'
import { anchoredVwap } from '@/lib/chartResearch'

const SUB_INFO_H = 16
const SUB_GAP = 4
const DEFAULT_VOLUME_COMPARE: VolumeCompareConfig = { enabled: true, days: 1 }

function normalizeVolumeCompare(config: VolumeCompareConfig): VolumeCompareConfig {
  return {
    enabled: config.enabled !== false,
    days: Math.max(1, Math.min(20, Math.round(Number(config.days) || 1))),
  }
}

interface Props {
  symbol: string
  height?: number
  className?: string
  dateRange?: { start: string; end: string }
  markers?: ChartMarker[]
  ranges?: ChartRange[]
  priceLines?: ChartPriceLine[]
  showLimitMarkers?: boolean
  showIndicatorControls?: boolean
  showMarkerToggle?: boolean
  showMA?: boolean
  showInfoBar?: boolean
  /** 初始可见蜡烛根数; 'all' = 适配显示全部数据 */
  visibleBars?: number | 'all'
  linkedPrice?: number | null
  onDateClick?: (date: string) => void
  onPriceDoubleClick?: (price: number, currentPrice: number) => void
  /** 扩展数据列参数（逗号分隔 config_id.field_name），透传给 klineDaily 接口 */
  extColumns?: string
  /** 加入自选日 (北京时间 YYYY-MM-DD); 有值时日K主图绘制「自选」竖虚线 */
  addedDate?: string | null
}

export const toOHLC = dailyChartRows

function buildLimitUpMarkers(rows: KlineRow[]): ChartMarker[] {
  const markers: ChartMarker[] = []
  for (const r of rows) {
    const date = typeof r.date === 'string' ? r.date.slice(0, 10) : String(r.date)
    if (r.signal_broken_limit_up) {
      markers.push({ date, kind: 'neutral', above: true, color: '#8B5CF6', label: '炸' })
    } else if (r.signal_limit_up) {
      const boards: number = r.consecutive_limit_ups ?? 1
      markers.push({ date, kind: 'buy', above: true, color: '#FACC15', label: boards <= 1 ? '板' : String(boards) })
    }
  }
  return markers
}

export function getDefaultRange(): { start: string; end: string } {
  const now = new Date()
  const end = now.toISOString().slice(0, 10)
  const s = new Date(now)
  s.setMonth(s.getMonth() - 6)
  const start = s.toISOString().slice(0, 10)
  return { start, end }
}

/** 工具栏小胶囊开关（指标与标注开关共用一套尺寸/关闭态样式，激活色由调用方给字面量）。 */
function ChartPill({
  active, label, activeClass, title, onClick,
}: {
  active: boolean
  label: string
  activeClass: string
  title?: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`px-2 py-0.5 rounded text-[10px] font-mono cursor-pointer transition-colors ${
        active ? activeClass : 'bg-elevated text-muted hover:text-secondary'
      }`}
    >
      {label}
    </button>
  )
}

export function StockDailyKChart({
  symbol,
  height = 520,
  className,
  dateRange: externalDateRange,
  markers,
  ranges,
  priceLines,
  showLimitMarkers = true,
  showIndicatorControls = true,
  showMarkerToggle = true,
  showMA = true,
  showInfoBar = true,
  visibleBars = 60,
  linkedPrice,
  onDateClick,
  onPriceDoubleClick,
  extColumns,
  addedDate,
}: Props) {
  const [activeIndicators, setActiveIndicators] = useState<string[]>(['vol'])
  const [showMarkers, setShowMarkers] = useState(true)
  const [requestedChartMode, setRequestedChartMode] = useState<'canvas' | 'professional'>('canvas')
  // 加入自选日标注（与「异动」标记相互独立）
  const [showAddedMark, setShowAddedMark] = useState(true)
  const [showResearch, setShowResearch] = useState(false)
  const [researchSelection, setResearchSelection] = useState({ context: '', date: '', anchor: '' })
  const [volumeCompare, setVolumeCompare] = useState<VolumeCompareConfig>(() =>
    normalizeVolumeCompare(storage.stockVolumeCompare.get(DEFAULT_VOLUME_COMPARE)),
  )
  const dateRange = externalDateRange ?? getDefaultRange()

  // 查询配置统一来自 klineDailyQueryOptions, 与 StockPanel 信息条/邻近预取共享同一 cache key (只发一次请求)
  const kline = useQuery({ ...klineDailyQueryOptions(symbol, dateRange, extColumns), enabled: !!symbol })

  const rows = useMemo(() => toOHLC(kline.data?.rows ?? []), [kline.data?.rows])
  const researchContext = [symbol, dateRange.start, dateRange.end].join('|')
  useEffect(() => {
    setResearchSelection(previous => previous.context === researchContext ? previous
      : { context: researchContext, date: '', anchor: '' })
  }, [researchContext])
  const selection = researchSelection.context === researchContext ? researchSelection : { date: '', anchor: '' }
  const anchored = useMemo(() => anchoredVwap(rows, selection.anchor), [rows, selection.anchor])
  const displayRows = useMemo(() => rows.map((row, index) => ({ ...row, anchored_vwap: anchored.values[index] })), [rows, anchored.values])
  const stockInfo = kline.data?.stock_info
  const limitMarkers = useMemo(() => buildLimitUpMarkers(kline.data?.rows ?? []), [kline.data?.rows])
  const allMarkers = useMemo(() => [
    ...(markers ?? []),
    ...(showLimitMarkers ? limitMarkers : []),
  ], [limitMarkers, markers, showLimitMarkers])
  const professionalReasons = useMemo(() => {
    const reasons: string[] = []
    if (ranges?.length) reasons.push('回测区间标注')
    if (addedDate) reasons.push('加入自选日标注')
    if (priceLines?.some(line => line.start != null || line.end != null)) reasons.push('带日期范围的价格线')
    return reasons
  }, [addedDate, priceLines, ranges])
  const requiresProfessional = professionalReasons.length > 0
  const chartMode = requiresProfessional ? 'professional' : requestedChartMode

  const toggleIndicator = useCallback((key: string) => {
    setActiveIndicators(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key])
  }, [])

  const updateVolumeCompare = useCallback((patch: Partial<VolumeCompareConfig>) => {
    setVolumeCompare(prev => {
      const next = normalizeVolumeCompare({ ...prev, ...patch })
      storage.stockVolumeCompare.set(next)
      return next
    })
  }, [])

  const activeSubDefs = activeIndicators
    .map(key => SUB_CHARTS.find(s => s.key === key))
    .filter((d): d is typeof SUB_CHARTS[number] => !!d)
  let subExtraH = 0
  activeSubDefs.forEach(def => { subExtraH += SUB_INFO_H + def.height })
  if (activeSubDefs.length > 0) subExtraH += activeSubDefs.length * SUB_GAP + 14
  const chartHeight = chartMode === 'professional' ? height + subExtraH : height

  if (!symbol) return null

  return (
    <div className={className} style={{ minHeight: chartHeight }}>
      <div className="flex flex-wrap items-center gap-1.5 pb-1" role="group" aria-label="日K图表模式">
        <button
          type="button"
          aria-pressed={chartMode === 'canvas'}
          disabled={requiresProfessional}
          title={requiresProfessional ? professionalReasons.join('、') + '仅在专业指标图中完整显示' : undefined}
          onClick={() => setRequestedChartMode('canvas')}
          className={chartMode === 'canvas'
            ? 'rounded px-2 py-0.5 text-[10px] transition-colors bg-accent/20 text-accent'
            : 'rounded px-2 py-0.5 text-[10px] transition-colors bg-elevated text-muted hover:text-secondary disabled:cursor-not-allowed disabled:opacity-40'}
        >
          自研Canvas
        </button>
        <button
          type="button"
          aria-pressed={chartMode === 'professional'}
          onClick={() => setRequestedChartMode('professional')}
          className={chartMode === 'professional'
            ? 'rounded px-2 py-0.5 text-[10px] transition-colors bg-accent/20 text-accent'
            : 'rounded px-2 py-0.5 text-[10px] transition-colors bg-elevated text-muted hover:text-secondary'}
        >
          专业指标
        </button>
        {requiresProfessional && (
          <span role="status" className="text-[10px] text-muted">
            {professionalReasons.join('、')}仅在专业指标图中完整显示，已默认切换以保留原标注。
          </span>
        )}
        {showIndicatorControls && <ChartPill active={showResearch} label="研究指标" activeClass="bg-accent/20 text-accent"
          title="查看趋势、波动与锚定VWAP；不改变交易规则" onClick={() => setShowResearch(value => !value)} />}
        {showIndicatorControls && chartMode === 'canvas' && showMarkerToggle && showLimitMarkers && (
          <ChartPill
            active={showMarkers}
            label="异动"
            activeClass="text-[#FACC15] bg-[#FACC15]/10"
            onClick={() => setShowMarkers(value => !value)}
          />
        )}
      </div>
      {showResearch && showIndicatorControls && <ChartResearchPanel symbol={symbol} data={rows}
        selectedDate={selection.date} anchorDate={selection.anchor} values={anchored.values} error={anchored.error}
        onSelectDate={date => setResearchSelection({ context: researchContext, date, anchor: selection.anchor })}
        onAnchorDate={anchor => setResearchSelection({ context: researchContext, date: selection.date, anchor })} />}
      {showIndicatorControls && chartMode === 'professional' && rows.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 px-1 pb-0.5">
          {SUB_CHARTS.map(ind => (
            <ChartPill
              key={ind.key}
              active={activeIndicators.includes(ind.key)}
              label={ind.label}
              activeClass="bg-accent/20 text-accent"
              onClick={() => toggleIndicator(ind.key)}
            />
          ))}
          {OVERLAY_INDICATORS.map(ind => (
            <ChartPill
              key={ind.key}
              active={activeIndicators.includes(ind.key)}
              label={ind.label}
              activeClass="bg-accent/20 text-accent"
              onClick={() => toggleIndicator(ind.key)}
            />
          ))}
          {activeIndicators.includes('vol') && (
            <div className="ml-0.5 flex h-5 items-center gap-1.5 border-l border-border/70 pl-2">
              <span className="text-[10px] text-muted">量比</span>
              <button
                type="button"
                role="switch"
                aria-checked={volumeCompare.enabled}
                aria-label="开启量能对比"
                title={volumeCompare.enabled ? '关闭量能对比' : '开启量能对比'}
                onClick={() => updateVolumeCompare({ enabled: !volumeCompare.enabled })}
                className={`relative h-3.5 w-6 shrink-0 rounded-full transition-colors ${
                  volumeCompare.enabled ? 'bg-accent' : 'bg-elevated'
                }`}
              >
                <span className={`absolute left-0 top-0.5 h-2.5 w-2.5 rounded-full bg-white transition-transform ${
                  volumeCompare.enabled ? 'translate-x-3' : 'translate-x-0.5'
                }`} />
              </button>
              <select
                aria-label="量能对比周期"
                value={volumeCompare.days}
                disabled={!volumeCompare.enabled}
                onChange={event => updateVolumeCompare({ days: Number(event.target.value) })}
                className="h-5 rounded border border-border bg-base px-1 text-[10px] text-secondary outline-none disabled:opacity-40"
              >
                {Array.from({ length: 20 }, (_, index) => index + 1).map(days => (
                  <option key={days} value={days}>前{days}日均量</option>
                ))}
              </select>
            </div>
          )}
          {/* 两个标注开关统一右对齐 */}
          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            {showMarkerToggle && showLimitMarkers && (
              <ChartPill
                active={showMarkers}
                label="异动"
                activeClass="text-[#FACC15] bg-[#FACC15]/10"
                onClick={() => setShowMarkers(v => !v)}
              />
            )}
            {/* 激活色与图表里的 ADDED_DATE_COLOR 一致; Tailwind 只认字面量类名, 故写死色值 */}
            {showMarkerToggle && addedDate && (
              <ChartPill
                active={showAddedMark}
                label="自选"
                activeClass="text-[#3B82F6] bg-[#3B82F6]/10"
                title={showAddedMark ? '隐藏K线上的加入自选日标注' : '显示K线上的加入自选日标注'}
                onClick={() => setShowAddedMark(v => !v)}
              />
            )}
          </div>
        </div>
      )}
      {kline.isLoading && <div className="text-sm text-muted py-4">加载中…</div>}
      {kline.isError && <div className="text-sm text-danger py-2">日K加载失败</div>}
      {!kline.isLoading && !kline.isError && (kline.data?.rows?.length ?? 0) > 0 && rows.length === 0 && (
        <div className="text-sm text-danger py-2">数据格式异常，请刷新页面</div>
      )}
      {rows.length > 0 && chartMode === 'canvas' && (
        <TradingViewDailyChart
          data={kline.data?.rows ?? []}
          symbol={symbol}
          height={height}
          showMA={showMA}
          showControls={showIndicatorControls}
          contextKey={[symbol, dateRange.start, dateRange.end].join('|')}
          markers={showMarkers ? allMarkers : []}
          priceLines={priceLines}
          linkedPrice={linkedPrice}
          onDateClick={onDateClick}
          onPriceDoubleClick={onPriceDoubleClick}
          visibleBars={visibleBars}
          anchoredVwap={rows.map((row, index) => ({ date: row.date, value: anchored.values[index] ?? null }))}
        />
      )}
      {rows.length > 0 && chartMode === 'professional' && (
        <EChartsCandlestick
          data={displayRows}
          markers={allMarkers}
          ranges={ranges}
          priceLines={priceLines}
          height={chartHeight - 22}
          showMA={showMA}
          showInfoBar={showInfoBar}
          showMarkers={showMarkers}
          stockInfo={stockInfo}
          symbol={symbol}
          linkedPrice={linkedPrice}
          onDateClick={onDateClick}
          onPriceDoubleClick={onPriceDoubleClick}
          visibleBars={visibleBars}
          activeIndicators={activeIndicators}
          volumeCompare={volumeCompare}
          addedDate={showAddedMark ? addedDate : null}
        />
      )}
    </div>
  )
}
