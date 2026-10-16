import { chartPrice } from '@/lib/chartPrice'
import { calculateTechnicalSummary, type TechnicalSummaryBar } from '@/lib/technicalSummary'

type Tone = 'positive' | 'negative' | 'caution' | 'cool' | 'muted'

const toneClasses: Record<Tone, string> = {
  positive: 'text-bull',
  negative: 'text-bear',
  caution: 'text-amber-300',
  cool: 'text-sky-300',
  muted: 'text-secondary',
}

function formatPct(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '不足'
  const normalized = Math.abs(value) < 0.005 ? 0 : value
  return (normalized > 0 ? '+' : '') + normalized.toFixed(2) + '%'
}

function indicatorValue(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '不足'
  return value === 0 ? '0' : Number(value.toPrecision(6)).toString()
}

function MetricCard({ title, state, detail, tone = 'muted' }: {
  title: string
  state: string
  detail: string
  tone?: Tone
}) {
  return <article className="min-w-0 rounded-lg border border-border/40 bg-elevated/20 p-3">
    <h3 className="text-[11px] font-medium text-muted">{title}</h3>
    <p className={'mt-1 text-xs font-semibold ' + toneClasses[tone]}>{state}</p>
    <p className="mt-1 break-words text-[10px] leading-relaxed text-secondary">{detail}</p>
  </article>
}

export function TechnicalSummary({
  rows,
  contextLabel,
  symbol,
}: {
  rows: readonly TechnicalSummaryBar[]
  contextLabel?: string
  symbol?: string
}) {
  const summary = calculateTechnicalSummary(rows)
  const price = (value: number | null) => value === null ? '不足' : chartPrice(value, symbol)

  const emaState = summary.emaTrend.status === 'above' ? 'EMA20 高于 EMA60'
    : summary.emaTrend.status === 'below' ? 'EMA20 低于 EMA60'
      : summary.emaTrend.status === 'equal' ? 'EMA20 与 EMA60 相等' : '不足'
  const rsiState = summary.rsi14.status === 'hot' ? '偏热（≥ 70）'
    : summary.rsi14.status === 'cold' ? '偏冷（≤ 30）'
      : summary.rsi14.status === 'normal' ? '区间内（30–70）' : '不足'
  const macdState = summary.macd.status === 'above' ? 'DIF 高于 DEA'
    : summary.macd.status === 'below' ? 'DIF 低于 DEA'
      : summary.macd.status === 'equal' ? 'DIF 与 DEA 相等' : '不足'
  const donchianState = summary.donchian20.status === 'breakout-up' ? '收盘高于前 20 根上轨'
    : summary.donchian20.status === 'breakout-down' ? '收盘低于前 20 根下轨'
      : summary.donchian20.status === 'inside' ? '收盘位于前 20 根通道内' : '不足'
  const emaTone: Tone = summary.emaTrend.status === 'above' ? 'positive'
    : summary.emaTrend.status === 'below' ? 'negative' : 'muted'
  const rsiTone: Tone = summary.rsi14.status === 'hot' ? 'caution'
    : summary.rsi14.status === 'cold' ? 'cool' : 'muted'
  const macdTone: Tone = summary.macd.status === 'above' ? 'positive'
    : summary.macd.status === 'below' ? 'negative' : 'muted'
  const donchianTone: Tone = summary.donchian20.status === 'breakout-up' ? 'positive'
    : summary.donchian20.status === 'breakout-down' ? 'negative' : 'muted'
  const performance = summary.performance
  const returnTone: Tone = performance.returnPct === null ? 'muted'
    : performance.returnPct > 0 ? 'positive' : performance.returnPct < 0 ? 'negative' : 'muted'
  const rangeLabel = summary.timeRange.orderedByTime
    ? (summary.timeRange.start ?? '—') + ' 至 ' + (summary.timeRange.end ?? '—')
    : '未提供可排序时间，按传入顺序'

  return <section aria-label="技术分项摘要与加载区间表现" className="mt-4 rounded-card border border-border/60 bg-surface/40 p-3 sm:p-4">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        <h2 className="text-xs font-semibold text-foreground">本地技术分项摘要</h2>
        <p className="mt-1 text-[10px] text-muted">研究技术状态，不是执行信号；不汇总为买卖结论。</p>
      </div>
      {contextLabel && <span className="rounded-full border border-border/50 bg-elevated/30 px-2 py-1 text-[10px] text-secondary">{contextLabel}</span>}
    </div>

    <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        title="EMA20 / EMA60"
        state={emaState}
        tone={emaTone}
        detail={summary.emaTrend.status === 'insufficient'
          ? '不足：至少需要 ' + summary.emaTrend.requiredBars + ' 根有效 K 线。'
          : 'EMA20 ' + price(summary.emaTrend.ema20) + ' · EMA60 ' + price(summary.emaTrend.ema60)}
      />
      <MetricCard
        title="RSI14"
        state={summary.rsi14.value === null ? rsiState : rsiState + ' · ' + summary.rsi14.value.toFixed(2)}
        tone={rsiTone}
        detail={summary.rsi14.status === 'insufficient'
          ? '不足：至少需要 ' + summary.rsi14.requiredBars + ' 根有效 K 线。'
          : '按收盘价计算 Wilder RSI。'}
      />
      <MetricCard
        title="MACD(12, 26, 9)"
        state={macdState}
        tone={macdTone}
        detail={summary.macd.status === 'insufficient'
          ? '不足：至少需要 ' + summary.macd.requiredBars + ' 根有效 K 线。'
          : 'DIF ' + indicatorValue(summary.macd.dif) + ' · DEA ' + indicatorValue(summary.macd.dea) + ' · 柱 ' + indicatorValue(summary.macd.histogram)}
      />
      <MetricCard
        title="Donchian 前 20 根"
        state={donchianState}
        tone={donchianTone}
        detail={summary.donchian20.status === 'insufficient'
          ? '不足：至少需要 ' + summary.donchian20.requiredBars + ' 根有效 K 线。'
          : '上轨 ' + price(summary.donchian20.previousUpper) + ' · 下轨 ' + price(summary.donchian20.previousLower)}
      />

      <article className="min-w-0 rounded-lg border border-border/40 bg-elevated/20 p-3 sm:col-span-2 xl:col-span-4">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h3 className="text-[11px] font-medium text-muted">当前加载区间表现</h3>
          <span className="text-[10px] text-muted">{summary.validBarCount} 根有效 K 线 · {rangeLabel}</span>
        </div>
        {performance.status === 'ready' ? <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div><p className="text-[10px] text-muted">首收至末收</p><p className={'mt-1 font-mono text-xs font-semibold ' + toneClasses[returnTone]}>{formatPct(performance.returnPct)}</p></div>
          <div><p className="text-[10px] text-muted">区间最高 / 最低</p><p className="mt-1 font-mono text-xs text-secondary">{price(performance.high)} / {price(performance.low)}</p></div>
          <div><p className="text-[10px] text-muted">最大回撤</p><p className="mt-1 font-mono text-xs text-bear">{formatPct(performance.maxDrawdownPct)}</p></div>
          <div><p className="text-[10px] text-muted">首收 / 末收</p><p className="mt-1 font-mono text-xs text-secondary">{price(performance.startClose)} / {price(performance.endClose)}</p></div>
        </div> : <p className="mt-2 text-[11px] text-muted">
          {performance.status === 'invalid-base' ? '基准收盘价无效，无法计算区间涨跌。'
            : performance.status === 'invalid-data' ? '区间 OHLC 数据无效，无法计算表现。'
              : '有效 K 线不足，无法计算区间表现。'}
        </p>}
        {performance.status === 'ready' && <p className="mt-2 text-[10px] text-muted">最高 / 最低取有效 K 线高低价；最大回撤按收盘价计算。无时间字段时按传入顺序计算涨跌。</p>}
      </article>
    </div>
  </section>
}
