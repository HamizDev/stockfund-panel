import { useEffect, useRef, useState } from 'react'
import { cryptoApi, type CryptoStrategyPreviewRequest, type CryptoStrategyPreviewResult } from './client'

const LABELS: Record<string, string> = {
  adx_14: 'ADX(14)', plus_di: '+DI', minus_di: '−DI', ema: 'EMA',
  atr: 'ATR', supertrend: 'Supertrend',
  bb_upper: '布林带上轨', bb_middle: '布林带中轨', bb_lower: '布林带下轨', aroon_up: 'AroonUp',
  momentum_price: '动量（价格单位）', kc_half_width: 'KC 半带宽（价格单位）',
  strength: 'ADX 高于门槛', three_bars_up: '连续3根多向', three_bars_down: '连续3根空向',
  above_ema: '收盘高于EMA', bullish_candle: '收盘高于开盘', previous_squeeze: '前根处于收缩',
  current_squeeze: '本根仍处于收缩', release: '本根释放', positive_momentum: '动量为正',
  trend_up: 'Supertrend 趋势向上', trend_flip: 'Supertrend 本根翻向',
  rebound_up: '回穿 EMA 向上', rebound_down: '回穿 EMA 向下',
  crossed_below_lower: '收盘下穿布林带下轨', crossed_below_upper: '收盘下穿布林带上轨',
  aroon_entry: 'AroonUp 高于 90 入场门槛', aroon_exit: 'AroonUp 低于 70 离场门槛',
}

export function StrategySignalPreview({ contextKey, getRequest }: {
  contextKey: string; getRequest: () => CryptoStrategyPreviewRequest
}) {
  const [saved, setSaved] = useState<{ key: string; result: CryptoStrategyPreviewResult } | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const active = useRef<AbortController | null>(null)
  useEffect(() => {
    active.current?.abort()
    setSaved(null)
    setPending(false)
    setError('')
    return () => { active.current?.abort() }
  }, [contextKey])
  const result = saved?.key === contextKey ? saved.result : null
  const preview = async () => {
    active.current?.abort()
    const controller = new AbortController()
    active.current = controller
    setError(''); setPending(true); setSaved(null)
    try {
      const response = await cryptoApi.strategyPreview(getRequest(), controller.signal)
      if (!controller.signal.aborted) setSaved({ key: contextKey, result: response })
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '信号预览失败')
    } finally {
      if (active.current === controller && !controller.signal.aborted) setPending(false)
    }
  }
  return <section aria-label="创建前信号预览" className="mt-3 rounded-btn border border-border bg-base/40 p-3 text-xs">
    <div className="flex flex-wrap items-center gap-3">
      <button type="button" disabled={pending} onClick={() => { void preview() }} className="rounded-btn border border-accent/40 bg-accent/10 px-3 py-2 text-accent disabled:opacity-50">{pending ? '读取公开行情…' : '预览当前规则信号'}</button>
      <span className="text-[11px] text-muted">使用表单参数和已收盘K线，只读预览，不创建账户或下单。</span>
    </div>
    {error && <p role="alert" className="mt-2 text-danger">{error}</p>}
    {result && <div className="mt-3 space-y-2" role="status">
      <p className="text-foreground">收盘信号：{({ long: '多向候选', short: '空向候选', hold: '保持当前仓位 / 空仓继续等待', flat: '空仓' } as Record<string, string>)[result.analysis.target] ?? result.analysis.target}</p>
      <p className="text-secondary">{result.analysis.reason}</p>
      <p className="text-[11px] text-muted">{result.evidence.symbol} · {result.evidence.interval} · {result.evidence.bars_count} 根已收盘K线 · 最后收盘 {new Date(result.evidence.data_end_ms).toLocaleString()}</p>
      <dl className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {Object.entries(result.analysis.metrics).map(([key, value]) => <div key={key} className="min-w-0 rounded-btn border border-border p-2"><dt className="text-muted">{LABELS[key] ?? key}</dt><dd className="mt-1 break-words font-mono">{Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 4 }) : '缺失'}</dd></div>)}
      </dl>
      <div className="flex flex-wrap gap-2">{Object.entries(result.analysis.conditions).map(([key, value]) => <span key={key} className="rounded-btn border border-border px-2 py-1 text-[11px] text-secondary">{LABELS[key] ?? key}：{value ? '是' : '否'}</span>)}</div>
      <p className="text-[11px] text-muted">公开成交手续费率 {(Number(result.evidence.taker_fee_rate) * 100).toFixed(4)}% / 单边 · 当前价差 {result.evidence.spread_bps.toFixed(2)} bps；信号不代表扣费后盈利。</p>
      {result.adaptation && <p className="text-[11px] text-muted">{result.adaptation}</p>}
      <p className="text-[11px] text-muted">{result.method}</p>
    </div>}
  </section>
}
