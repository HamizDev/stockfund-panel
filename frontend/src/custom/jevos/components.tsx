import { useQuery } from '@tanstack/react-query'
import { QK } from '@/lib/queryKeys'
import { jevosApi } from './client'
import type { JevosReview, JevosStatus } from './types'

const NEWS_LABELS = {
  positive_clue: '偏利好', risk_clue: '偏利空', neutral: '中性', mixed: '正负混合', unclear: '信息不足',
} as const
const STRATEGY_LABELS = {
  support: '原信号获支持', wait: '建议等待', conflict: '信号冲突', insufficient: '信息不足',
} as const
const NEWS_PROBABILITIES: Record<string, string> = {
  positive: '偏利好', negative: '偏利空', neutral: '中性', mixed: '正负混合', uncertain: '信息不足',
  risk: '风险线索', material: '事件实质性',
}
const STRATEGY_PROBABILITIES: Record<string, string> = {
  supports_signal: '支持原信号', conflict: '与原信号冲突', sufficient: '信息充分度',
}

function timestamp(value: number | null | undefined) {
  return value && Number.isFinite(value) ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚未取得'
}

function probabilityText(review: JevosReview, labels: Record<string, string>) {
  return Object.entries(review.probabilities)
    .filter(([key, value]) => labels[key] && Number.isFinite(value))
    .map(([key, value]) => `${labels[key]} ${Math.round(value * 100)}%`)
}

function reviewLabel(review: JevosReview) {
  if (review.kind === 'news') return NEWS_LABELS[review.label as keyof typeof NEWS_LABELS] ?? '信息不足'
  return STRATEGY_LABELS[review.label as keyof typeof STRATEGY_LABELS] ?? '信息不足'
}

function labelTone(review: JevosReview) {
  if (review.label === 'positive_clue' || review.label === 'support') return 'border-emerald-400/25 bg-emerald-400/5 text-emerald-300'
  if (review.label === 'risk_clue' || review.label === 'conflict') return 'border-amber-400/30 bg-amber-400/5 text-amber-300'
  return 'border-violet-400/25 bg-violet-400/5 text-violet-300'
}

export function useJevosStatus() {
  return useQuery({
    queryKey: QK.jevosStatus,
    queryFn: ({ signal }) => jevosApi.status(signal),
    retry: false,
    staleTime: 0,
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
  })
}

function statusLabel(status: JevosStatus['status'] | undefined, failed: boolean) {
  if (failed) return '状态暂不可用'
  if (status === 'ready') return '本机服务就绪'
  if (status === 'starting') return '正在启动'
  if (status === 'not_ready') return '模型尚未就绪'
  if (status === 'invalid_response') return '模型响应异常'
  if (status === 'offline') return '本机服务未运行'
  return '正在检查'
}

export function JevosNewsStatus({ status, isLoading, isError }: {
  status?: JevosStatus
  isLoading: boolean
  isError: boolean
}) {
  const count = Object.keys(status?.news ?? {}).length
  return <section aria-label="Jevos 本地快讯初筛" className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-border bg-surface px-4 py-2.5 text-xs">
    <span className="font-semibold text-foreground" title="优先关注标的，自动初筛最近最多200条；每批最多40条，复用本机缓存。">Jevos 本地初筛</span>
    <span className={status?.status === 'ready' ? 'text-emerald-300' : 'text-muted'}>{statusLabel(status?.status, isError)}</span>
    <span className="text-muted">已记录 {count} 条 · 队列 {status?.queued ?? '—'} · 完成 {status?.completed ?? '—'}</span>
    {status?.model && <span className="text-muted" title={`模型状态更新时间：${timestamp(status.last_check_ms)}`}>{status.model}</span>}
    {isLoading && !status && <span className="text-muted">正在连接本机辅助服务…</span>}
    <span className="ml-auto text-[11px] text-muted">判断原文所述事件；不代表全市场方向，不参与交易</span>
  </section>
}

export function JevosNewsReview({ review }: { review?: JevosReview }) {
  if (!review) return null
  const probabilities = probabilityText(review, NEWS_PROBABILITIES)
  return <details className={`mt-2 rounded border px-3 py-2 text-xs ${labelTone(review)}`}>
    <summary className="cursor-pointer font-medium">Jevos 初筛 · {reviewLabel(review)}</summary>
    <div className="mt-2 space-y-1.5 leading-relaxed text-secondary">
      <p>模型 {review.model} · 判断于 {timestamp(review.generated_at_ms)}</p>
      {review.reason && <p>{review.reason}</p>}
      {review.tentative && <p className="text-amber-300">判断较弱，保留方向倾向供筛选。</p>}
      {review.evidence?.map((quote, index) => <blockquote key={index} className="border-l-2 border-border pl-2 text-muted">原文：{quote}</blockquote>)}
      {review.confidence != null && <p className="text-muted">模型区分度 {Math.round(review.confidence * 100)}%（未经市场准确率校准）</p>}
      {probabilities.length > 0 && <p><span className="text-muted">模型判断概率（不是胜率）：</span>{probabilities.join(' · ')}</p>}
      {review.related_names.length > 0 && <div>
        <span className="text-muted">关联关注 / 登记持仓：</span>
        <span>{review.related_names.join('、')}</span>
        <p className="mt-1 text-[11px] text-muted">这是名称关联线索，不代表相关标的一定受益。</p>
      </div>}
    </div>
  </details>
}

function signalText(signal: string | null | undefined) {
  if (signal === 'long') return '多头'
  if (signal === 'short') return '空头'
  if (signal === 'flat') return '空仓'
  if (signal === 'hold') return '保持'
  return '暂无'
}

function isHistoricalReview(review: JevosReview, lastBarMs: number | null) {
  return review.source_at_ms == null || (lastBarMs != null && review.source_at_ms < lastBarMs)
}

export function JevosStrategyShadow({ status, isLoading, isError, bots }: {
  status?: JevosStatus
  isLoading: boolean
  isError: boolean
  bots: { id: string; name: string; last_bar_ms: number | null; last_signal: string | null }[]
}) {
  const history = (status?.strategy_history ?? []).slice(0, 10)
  return <section aria-label="Jevos 策略辅助意见" className="space-y-3 rounded-card border border-border bg-surface p-3 md:p-4">
    <div className="flex flex-wrap items-start gap-3">
      <div className="min-w-0 flex-1">
        <h3 className="text-sm font-semibold text-foreground">Jevos 本地策略对照</h3>
        <p className="mt-1 text-[11px] leading-relaxed text-muted">本地辅助，未参与下单；只记录对照意见。模型判断概率不是胜率，策略信号和风控仍由原规则控制。</p>
      </div>
      <span className="text-xs text-muted">{statusLabel(status?.status, isError)}{status?.model ? ` · ${status.model}` : ''}</span>
    </div>
    {isLoading && !status && <p role="status" className="rounded-btn border border-dashed border-border p-3 text-xs text-muted">正在读取本机辅助意见…</p>}
    {isError && !status && <p role="status" className="rounded-btn border border-border bg-base p-3 text-xs text-muted">本机辅助意见暂不可用；策略监控和模拟交易状态不受影响。</p>}
    {bots.length > 0 && <div className="grid gap-2 xl:grid-cols-3">
      {bots.map(bot => {
        const review = status?.strategies[bot.id]
        const historical = review ? isHistoricalReview(review, bot.last_bar_ms) : false
        const probabilities = review ? probabilityText(review, STRATEGY_PROBABILITIES) : []
        return <article key={bot.id} className="rounded-btn border border-border bg-base p-3 text-xs">
          <h4 className="font-semibold text-foreground">{bot.name}</h4>
          <div className="mt-2 space-y-1 leading-relaxed text-muted">
            <p>最近收盘 K 线：{timestamp(bot.last_bar_ms)}</p>
            <p>原策略信号：{signalText(review?.original_signal ?? bot.last_signal)}</p>
            {review ? <>
              <p>意见更新时间：{timestamp(review.generated_at_ms)}</p>
              <p className={historical ? 'text-amber-300' : 'text-foreground'}>{historical ? '基于旧 K 线的历史意见' : reviewLabel(review)}</p>
              {probabilities.length > 0 && <p title="模型判断概率不是胜率">模型判断概率（非胜率）：{probabilities.join(' · ')}</p>}
            </> : <p>{status?.status === 'ready' ? '等待下一根已收盘 K 线的对照意见。' : '本机辅助服务就绪后会记录对照意见。'}</p>}
          </div>
        </article>
      })}
    </div>}
    {history.length > 0 && <details className="rounded-btn border border-border bg-base">
      <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-secondary">最近 {history.length} 条策略意见</summary>
      <div className="space-y-2 border-t border-border p-3">
        {history.map((review, index) => {
          const bot = bots.find(item => item.id === review.id)
          const historical = isHistoricalReview(review, bot?.last_bar_ms ?? null)
          return <div key={`${review.input_key}-${index}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
            <span className="font-medium text-foreground">{bot?.name ?? '策略记录'}</span>
            <span>原信号 {signalText(review.original_signal)}</span>
            <span>{reviewLabel(review)}{historical ? ' · 历史意见' : ''}</span>
            <span className="ml-auto">{timestamp(review.generated_at_ms)}</span>
          </div>
        })}
      </div>
    </details>}
  </section>
}
