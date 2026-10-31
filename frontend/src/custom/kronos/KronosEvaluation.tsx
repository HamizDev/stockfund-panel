import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { formatKronosPrice, formatKronosTime } from './chartModel'
import type { KronosAssetType } from './state'
import type { KronosJob } from './types'
import type { KronosEvaluation as KronosEvaluationData } from './evaluationTypes'

const POLLING_STATES = new Set<KronosEvaluationData['state']>(['pending', 'partial', 'unavailable'])

export function formatKronosEvaluationMetric(value: number | null): string {
  return value === null ? '—' : value.toFixed(6)
}

function formatPercent(value: number | null): string {
  return value === null ? '—' : `${(value * 100).toFixed(2)}%`
}

function EvaluationMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded bg-elevated/70 px-2 py-1.5">
      <div className="text-muted">{label}</div>
      <div className="mt-0.5 truncate font-mono text-secondary" title={value}>{value}</div>
    </div>
  )
}

function stateLabel(state: KronosEvaluationData['state']): string {
  if (state === 'pending') return '等待真实行情'
  if (state === 'partial') return '部分完成'
  if (state === 'complete') return '评估完成'
  if (state === 'unavailable') return '行情暂不可用'
  return '暂不适用'
}

function PointRows({
  evaluation,
  job,
  assetType,
}: {
  evaluation: KronosEvaluationData
  job: KronosJob
  assetType?: KronosAssetType
}) {
  if (evaluation.points.length === 0) return null

  return (
    <div className="overflow-x-auto rounded border border-border/30">
      <table className="w-full min-w-[620px] border-collapse text-left text-[10px]">
        <thead className="bg-elevated/70 text-muted">
          <tr>
            <th className="px-2 py-1.5 font-medium">时点（北京时间）</th>
            <th className="px-2 py-1.5 font-medium">预测收盘</th>
            <th className="px-2 py-1.5 font-medium">真实收盘</th>
            <th className="px-2 py-1.5 font-medium">误差</th>
            <th className="px-2 py-1.5 font-medium">方向结果</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/20">
          {evaluation.points.map(point => {
            const unmatchedLabel = point.status === 'pending'
              ? '等待对应真实行情完成'
              : '缺少对应真实行情报价'
            return (
              <tr key={point.time} className="text-secondary">
                <td className="px-2 py-1.5 font-mono">{formatKronosTime(point.time, job.request.period)}</td>
                <td className="px-2 py-1.5 font-mono">{formatKronosPrice(point.predicted_close, assetType)}</td>
                <td className="px-2 py-1.5 font-mono">
                  {point.actual_close === null ? <span className="text-muted">{unmatchedLabel}</span> : formatKronosPrice(point.actual_close, assetType)}
                </td>
                <td className="px-2 py-1.5 font-mono">
                  {formatKronosEvaluationMetric(point.error)}
                </td>
                <td className="px-2 py-1.5">
                  {point.direction_match === null ? '—' : point.direction_match ? '一致' : '不一致'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function KronosEvaluation({
  job,
  assetType,
  initialEvaluation,
}: {
  job: KronosJob
  assetType?: KronosAssetType
  /** Comparison details already carry the selected trial evaluation in one batch response. */
  initialEvaluation?: KronosEvaluationData | null
}) {
  const hasInitialEvaluation = initialEvaluation !== undefined
  const evaluationQuery = useQuery({
    queryKey: QK.kronosEvaluation(job.id),
    queryFn: ({ signal }) => api.kronosEvaluation(job.id, signal),
    enabled: !hasInitialEvaluation,
    staleTime: 30_000,
    retry: false,
    refetchIntervalInBackground: false,
    refetchInterval: query => {
      if (hasInitialEvaluation) return false
      const evaluation = query.state.data as KronosEvaluationData | undefined
      return evaluation && POLLING_STATES.has(evaluation.state) ? 30_000 : false
    },
  })

  const evaluation = hasInitialEvaluation ? initialEvaluation : evaluationQuery.data
  const metrics = evaluation?.metrics
  const hasMatchedPoints = Boolean(evaluation && evaluation.matched > 0)

  return (
    <section className="rounded-md border border-border/40 bg-surface/70 p-3 sm:p-4" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-xs font-semibold text-foreground">自动对照真实行情</h3>
          <p className="mt-1 text-[10px] text-muted">自动评估已开启 · 保留原模型，未启用训练/自动替换</p>
        </div>
        {!hasInitialEvaluation && <button
          type="button"
          onClick={() => { void evaluationQuery.refetch() }}
          disabled={evaluationQuery.isFetching}
          className="inline-flex h-7 items-center gap-1 rounded border border-border/50 bg-elevated px-2 text-[10px] text-secondary hover:text-foreground disabled:opacity-50"
        >
          <RefreshCw className={cn('h-3 w-3', evaluationQuery.isFetching && 'animate-spin')} />
          刷新评估状态
        </button>}
      </div>

      {!hasInitialEvaluation && evaluationQuery.isPending && !evaluation && (
        <p className="mt-3 text-[11px] text-secondary">正在读取自动评估状态…</p>
      )}

      {!hasInitialEvaluation && evaluationQuery.isError && !evaluation && (
        <div role="status" className="mt-3 rounded border border-amber-500/25 bg-amber-500/5 px-2.5 py-2 text-[11px] text-secondary">
          暂时无法读取评估结果。{evaluationQuery.error instanceof Error ? evaluationQuery.error.message : '请稍后重试。'}
        </div>
      )}

      {hasInitialEvaluation && !evaluation && (
        <p className="mt-3 text-[11px] text-secondary">当前试验尚无评估快照；页面会随比较批次状态一并刷新。</p>
      )}

      {evaluation && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-[10px]">
            <span className={cn(
              'rounded px-2 py-1',
              evaluation.state === 'complete' ? 'bg-bull/10 text-bull' : 'bg-elevated text-secondary',
            )}>
              {stateLabel(evaluation.state)}
            </span>
            {evaluation.evaluated_at && (
              <span className="text-muted">评估更新时间（北京时间）：{formatKronosTime(evaluation.evaluated_at, '1m')}</span>
            )}
          </div>

          {evaluation.state === 'pending' && (
            <p className="rounded border border-accent/20 bg-accent/5 px-2.5 py-2 text-[11px] text-secondary">
              等待对应真实行情完成。行情尚未形成前不计入误差统计。
            </p>
          )}

          {evaluation.state === 'partial' && (
            <p className="rounded border border-accent/20 bg-accent/5 px-2.5 py-2 text-[11px] text-secondary">
              已核对 {evaluation.matched}/{evaluation.expected} · 待形成 {evaluation.pending} · 缺报价 {evaluation.missing}。
              缺报价点不计为预测错误。
            </p>
          )}

          {evaluation.state === 'unavailable' && (
            <p className="rounded border border-amber-500/25 bg-amber-500/5 px-2.5 py-2 text-[11px] text-secondary">
              {evaluation.message || '真实行情数据暂不可用，系统会继续检查。'}
            </p>
          )}

          {evaluation.state === 'not_applicable' && (
            <p className="rounded border border-border/30 bg-elevated/40 px-2.5 py-2 text-[11px] text-secondary">
              {evaluation.message || '该预测暂不适用于自动行情对照。'}
            </p>
          )}

          {(evaluation.state === 'complete' || (evaluation.state === 'partial' && hasMatchedPoints)) && (
            <>
              {evaluation.state === 'complete' && (
                <p className="text-[10px] text-secondary">
                  已核对 {evaluation.matched}/{evaluation.expected} 个时点 · 缺报价 {evaluation.missing}；缺报价点不计为预测错误。
                </p>
              )}
              <div className="grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-3 lg:grid-cols-5">
                <EvaluationMetric label="MAE（已核对点）" value={formatKronosEvaluationMetric(hasMatchedPoints ? metrics?.mae ?? null : null)} />
                <EvaluationMetric label="RMSE（已核对点）" value={formatKronosEvaluationMetric(hasMatchedPoints ? metrics?.rmse ?? null : null)} />
                <EvaluationMetric label="MAPE（已核对点）" value={formatPercent(hasMatchedPoints ? metrics?.mape ?? null : null)} />
                <EvaluationMetric label="基准 MAE（已核对点）" value={formatKronosEvaluationMetric(hasMatchedPoints ? metrics?.baseline_mae ?? null : null)} />
                <EvaluationMetric
                  label="方向一致率"
                  value={hasMatchedPoints && metrics?.direction_total
                    ? `${metrics.direction_matches}/${metrics.direction_total} · ${formatPercent(metrics.direction_accuracy)}`
                    : '—'}
                />
              </div>
              <p className="text-[10px] text-muted">
                指标仅按已核对时点计算；方向一致率按已核对时点计算，不是交易胜率。
                {metrics && ` 平盘真实行情 ${metrics.flat_actual_count} 个时点。`}
                {evaluation.reference_close !== null && ` 方向参考（预测生成时最后真实收盘价）${formatKronosPrice(evaluation.reference_close, assetType)}。`}
              </p>
            </>
          )}

          {evaluation.points.some(point => point.status === 'pending') && (
            <p className="text-[10px] text-muted">等待对应真实行情完成的预测点暂不展示为零误差。</p>
          )}

          <PointRows evaluation={evaluation} job={job} assetType={assetType} />
        </div>
      )}

      {!hasInitialEvaluation && evaluationQuery.isError && evaluation && (
        <p className="mt-2 text-[10px] text-amber-400">刷新评估失败，仍显示上次已读取结果。</p>
      )}
    </section>
  )
}
