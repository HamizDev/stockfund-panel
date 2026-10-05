import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CalendarClock, Loader2, RotateCcw, Sparkles } from 'lucide-react'
import { api, type HoldingsDailyReviewScope } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'

/** 只用作前端缓存分区，不把登记的代码、金额或数量直接放进 query key。 */
export function holdingsReviewSignature(rows: readonly unknown[]): string {
  const canonicalize = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonicalize)
    if (value && typeof value === 'object') {
      return Object.fromEntries(
        Object.entries(value as Record<string, unknown>)
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([key, item]) => [key, canonicalize(item)]),
      )
    }
    return value
  }

  const canonical = rows.map(row => JSON.stringify(canonicalize(row))).sort().join('\n')
  let first = 0x811c9dc5
  let second = 0x9e3779b9
  for (let index = 0; index < canonical.length; index++) {
    const code = canonical.charCodeAt(index)
    first = Math.imul(first ^ code, 0x01000193)
    second = Math.imul(second ^ code, 0x85ebca6b)
    second ^= second >>> 13
  }
  return `${rows.length}-${(first >>> 0).toString(16)}${(second >>> 0).toString(16)}`
}

function generatedAt(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}

const scopeTitle: Record<HoldingsDailyReviewScope, string> = {
  lots: '股票 / ETF 持仓日报',
  fund: '基金持仓日报',
}

export function HoldingsDailyReview({
  scope,
  holdingSignature,
  loaded,
  holdingsRefreshError = '',
}: {
  scope: HoldingsDailyReviewScope
  holdingSignature: string
  loaded: boolean
  holdingsRefreshError?: string
}) {
  const queryClient = useQueryClient()
  const queryKey = QK.holdingsDailyReview(scope, holdingSignature)
  const autoStartedDays = useRef(new Set<string>())
  const statusQuery = useQuery({
    queryKey,
    queryFn: () => api.holdingsDailyReview(scope),
    enabled: loaded,
    retry: false,
    refetchInterval: query => query.state.data?.status === 'running' ? 3000 : 60000,
    refetchOnWindowFocus: true,
  })
  const startMutation = useMutation({
    mutationFn: (force: boolean) => api.startHoldingsDailyReview(scope, force),
    onSuccess: state => queryClient.setQueryData(queryKey, state),
  })
  const state = statusQuery.data
  const mutate = startMutation.mutate

  useEffect(() => {
    if (!loaded || !state || state.status !== 'not_generated' || state.holdings_count <= 0) return
    if (!state.today || autoStartedDays.current.has(state.today)) return
    // 以服务端返回的北京时间日期为准，同一页面生命周期一天最多自动调用一次。
    autoStartedDays.current.add(state.today)
    mutate(false)
  }, [loaded, mutate, state])

  const manualGenerate = () => {
    startMutation.reset()
    mutate(true)
  }

  const report = state?.report ?? null
  const showPrevious = !!report && report.report_date !== state?.today
  const canManuallyGenerate = !!state && state.status !== 'running' && state.status !== 'empty' && state.status !== 'unconfigured'

  return (
    <section className="overflow-hidden rounded-xl border border-border bg-surface" aria-labelledby={`holdings-review-${scope}`}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/70 px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <Sparkles className="h-4 w-4 shrink-0 text-accent" />
          <h2 id={`holdings-review-${scope}`} className="text-sm font-semibold text-foreground">{scopeTitle[scope]}</h2>
          {state?.today && <span className="text-[10px] text-muted">服务端日期 {state.today}</span>}
        </div>
        {state && state.holdings_count > 0 && (
          <span className="text-[10px] text-muted">{state.holdings_count} 项登记持仓</span>
        )}
      </div>

      <div className="space-y-3 p-4">
        <p className="text-[11px] leading-5 text-muted">
          登记持仓及行情研究资料会发送给已配置的 AI，生成条件式研究建议，不修改持仓、不下单。登记值不是实时券商余额；首次打开当天自动生成会使用 AI 额度。
        </p>
        {holdingsRefreshError && (
          <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs text-secondary" role="status">
            持仓列表最近一次刷新失败，当前日报仍对应最近成功读取的登记持仓：{holdingsRefreshError}
          </div>
        )}

        {!loaded || statusQuery.isLoading ? (
          <div className="flex items-center gap-2 py-2 text-xs text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在读取今日分析状态…</div>
        ) : statusQuery.isError && !state ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-danger/30 bg-danger/5 p-3" role="alert">
            <span className="text-xs text-danger">日报状态读取失败：{statusQuery.error instanceof Error ? statusQuery.error.message : '请稍后重试'}</span>
            <button onClick={() => void statusQuery.refetch()} className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:bg-elevated">重试读取</button>
          </div>
        ) : state?.status === 'empty' || (state?.holdings_count ?? 0) === 0 ? (
          <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted">当前没有已登记持仓，暂不生成日报。</div>
        ) : state?.status === 'unconfigured' ? (
          <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs text-secondary">
            尚未配置 AI。请先到设置页启用 AI 模型，之后打开本页即可生成持仓日报。
          </div>
        ) : state?.status === 'running' || startMutation.isPending ? (
          <div className="flex items-center gap-2 rounded-lg border border-accent/25 bg-accent/5 p-3 text-xs text-secondary" role="status">
            <Loader2 className="h-4 w-4 animate-spin text-accent" />正在生成今日持仓分析，完成后会自动显示。
          </div>
        ) : state?.status === 'failed' ? (
          <div className="rounded-lg border border-danger/30 bg-danger/5 p-3" role="alert">
            <div className="flex items-start gap-2 text-xs text-danger">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{report?.error ?? '今日分析未完成。可检查行情日期和 AI 配置后手动重试。'}</span>
            </div>
          </div>
        ) : state?.status === 'not_generated' ? (
          startMutation.isError ? (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-danger/30 bg-danger/5 p-3" role="alert">
              <span className="text-xs text-danger">自动生成请求未成功：{startMutation.error instanceof Error ? startMutation.error.message : '请稍后重试'}</span>
              <button onClick={manualGenerate} disabled={startMutation.isPending} className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:bg-elevated disabled:opacity-50">重试生成（会调用 AI）</button>
            </div>
          ) : (
            <div className="flex items-center gap-2 py-2 text-xs text-muted" role="status">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />正在启动今日分析…
            </div>
          )
        ) : null}

        {statusQuery.isError && state && (
          <div className="text-[11px] text-warning" role="status">日报状态暂时刷新失败，仍保留最近一次成功读取的结果。</div>
        )}

        {state && report && state.holdings_count > 0 && (
          <div className="space-y-3">
            {(state.holdings_changed || state.model_changed) && (
              <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs text-secondary" role="status">
                {state.holdings_changed && <span>登记持仓在这份报告生成后有变化。 </span>}
                {state.model_changed && <span>当前 AI 模型与生成报告时不同。 </span>}
                可手动重新生成以纳入最新持仓或模型。
              </div>
            )}
            <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] text-muted">
              <span className="inline-flex items-center gap-1.5">
                <CalendarClock className="h-3.5 w-3.5" />
                {showPrevious ? `上一份日报 · ${report.report_date}` : `报告日期 · ${report.report_date}`}
                {' · '}生成于北京时间 {generatedAt(report.generated_at_ms)} · {report.model || '模型未注明'}
              </span>
              {canManuallyGenerate && (
                <button
                  onClick={manualGenerate}
                  disabled={startMutation.isPending}
                  className="inline-flex min-h-8 items-center gap-1.5 rounded-btn border border-border px-2.5 text-[11px] text-secondary transition-colors hover:border-accent/50 hover:text-accent disabled:cursor-wait disabled:opacity-50"
                  title="重新调用 AI，会消耗已配置模型额度"
                >
                  {startMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
                  重新生成（会调用 AI）
                </button>
              )}
            </div>
            {startMutation.isError && (
              <div className="text-xs text-danger" role="alert">生成请求未完成：{startMutation.error instanceof Error ? startMutation.error.message : '请稍后手动重试'}</div>
            )}
            {!!report.warnings.length && (
              <div className="rounded-lg border border-warning/25 bg-warning/5 p-3 text-xs text-secondary">
                <div className="mb-1 font-medium text-warning">数据提示</div>
                <ul className="list-disc space-y-1 pl-4">{report.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul>
              </div>
            )}
            {report.observations.length > 0 && (
              <details className="rounded-lg border border-border/70 bg-base/40 px-3 py-2">
                <summary className="cursor-pointer text-[11px] text-secondary">数据覆盖与日期（{report.observations.length} 项）</summary>
                <ul className="mt-2 divide-y divide-border/50 text-[10px]">
                  {report.observations.map((item, index) => (
                    <li key={`${item.symbol}-${index}`} className="flex flex-wrap justify-between gap-x-3 gap-y-1 py-1.5">
                      <span className="font-mono text-foreground">{item.name || item.symbol} <span className="text-muted">{item.symbol} · {item.asset_type}</span></span>
                      <span className="text-muted">数据日 {item.data_date || '未知'}{item.warning ? ` · ${item.warning}` : ''}</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {report.content.trim() && (
              <div className="border-t border-border/60 pt-3 text-xs leading-6 text-secondary">
                <MarkdownRenderer content={report.content} />
              </div>
            )}
          </div>
        )}

        {!report && state && state.status !== 'running' && state.status !== 'not_generated' && state.status !== 'empty' && state.status !== 'unconfigured' && (
          <button
            onClick={manualGenerate}
            disabled={!canManuallyGenerate || startMutation.isPending}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-btn border border-accent/35 bg-accent/10 px-3 text-xs font-medium text-accent transition-colors hover:bg-accent/15 disabled:cursor-wait disabled:opacity-50"
            title="重新调用 AI，会消耗已配置模型额度"
          >
            {startMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
            重新生成（会调用 AI）
          </button>
        )}
      </div>
    </section>
  )
}
