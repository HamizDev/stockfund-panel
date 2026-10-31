import { useEffect, useRef, useState } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, AlertCircle, AlertTriangle, CheckCircle2, Clock3, Cpu, Loader2, Play, RefreshCw, Square } from 'lucide-react'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { PageHeader } from '@/components/PageHeader'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { KronosChart } from './KronosChart'
import { KronosEvaluation } from './KronosEvaluation'
import {
  KRONOS_HORIZONS,
  KRONOS_LOOKBACKS,
  KRONOS_SAMPLE_COUNTS,
  kronosSearchParams,
  normalizeKronosLookback,
  readKronosViewState,
  writeKronosViewState,
  type KronosAssetType,
  type KronosViewState,
} from './state'
import { formatKronosTime } from './chartModel'
import {
  readKronosWorkbenchSession,
  updateKronosWorkbenchSession,
  type KronosWorkbenchTab,
} from './workbenchSession'
import {
  isKronosJobActive,
  KRONOS_CLOSE_ONLY_WARNING,
  type KronosJob,
  type KronosJobRequest,
  type KronosPeriod,
} from './types'
import { KronosCandidatePanel, KronosComparisonPanel, KronosSummaryPanel } from './KronosWorkbench'

const KRONOS_TABS: Array<{ id: KronosWorkbenchTab; label: string }> = [
  { id: 'single', label: '单次预测' },
  { id: 'comparison', label: '配置对比' },
  { id: 'summary', label: '成绩汇总' },
  { id: 'candidates', label: '候选模型' },
]

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请稍后重试'
}

function stateLabel(state: KronosJob['state']): string {
  if (state === 'preparing') return '准备中'
  if (state === 'running') return '推理中'
  if (state === 'complete') return '已完成'
  if (state === 'cancelled') return '已取消'
  return '失败'
}

function modelLabel(model: KronosJobRequest['model']): string {
  return model === 'small' ? 'Small' : 'Base'
}

function periodLabel(period: KronosPeriod): string {
  return period === 'daily' ? '日K' : '1分钟K'
}

function SectionCard({ children, className }: { children: React.ReactNode; className?: string }) {
  return <section className={cn('rounded-md border border-border/40 bg-surface/70 p-3 sm:p-4', className)}>{children}</section>
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-[11px] text-secondary">
      <span>{label}</span>
      {children}
    </label>
  )
}

const selectClass = 'h-8 w-full rounded border border-border bg-elevated px-2 text-xs text-foreground focus:border-accent/50 focus:outline-none'

export function KronosPage() {
  const location = useLocation()
  const [, setSearchParams] = useSearchParams()
  const [view, setView] = useState<KronosViewState>(() => readKronosViewState(location.search))
  const [initialWorkbenchSession] = useState(readKronosWorkbenchSession)
  const [activeTab, setActiveTab] = useState<KronosWorkbenchTab>(() => initialWorkbenchSession.activeTab)
  const queryClient = useQueryClient()
  const previousWorkbenchScope = useRef(initialWorkbenchSession.scope)

  useEffect(() => writeKronosViewState(view), [view])

  useEffect(() => {
    const currentScope = view.selection ? { symbol: view.selection.symbol, period: view.period } : null
    const previousScope = previousWorkbenchScope.current
    const sameScope = previousScope === null
      ? currentScope === null
      : currentScope?.symbol === previousScope.symbol && currentScope.period === previousScope.period
    if (sameScope) return

    const stored = readKronosWorkbenchSession()
    if (stored.selectedBatchId) {
      queryClient.removeQueries({ queryKey: QK.kronosComparison(stored.selectedBatchId), exact: true })
    }
    updateKronosWorkbenchSession({ scope: currentScope, selectedBatchId: null, selectedTrialId: null })
    previousWorkbenchScope.current = currentScope
  }, [view.selection?.symbol, view.period, queryClient])

  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const symbol = params.get('symbol')?.trim()
    if (!symbol) return
    const queryPeriod = params.get('period')
    const queryAssetType = params.get('assetType')
    const queryJob = params.get('job')
    setView(current => {
      const sameTarget = current.selection?.symbol === symbol
      const assetType: KronosAssetType | undefined = queryAssetType === 'stock' || queryAssetType === 'etf'
        ? queryAssetType
        : sameTarget ? current.selection?.assetType : undefined
      const period: KronosPeriod = queryPeriod === 'daily' || queryPeriod === '1m' ? queryPeriod : current.period
      return {
        ...current,
        jobId: queryJob && /^[0-9a-f]{32}$/.test(queryJob) ? queryJob : current.jobId,
        period,
        lookback: normalizeKronosLookback(period, current.lookback),
        selection: {
          symbol,
          name: params.get('name')?.trim() || (sameTarget ? current.selection?.name : undefined) || symbol,
          ...(assetType ? { assetType } : {}),
        },
      }
    })
  }, [location.search])

  const statusQuery = useQuery({
    queryKey: QK.kronosStatus,
    queryFn: ({ signal }) => api.kronosStatus(signal),
    enabled: activeTab === 'single',
    staleTime: 0,
    refetchOnMount: 'always',
    retry: false,
    refetchIntervalInBackground: false,
    refetchInterval: query => activeTab === 'single' && query.state.data?.busy ? 1500 : false,
  })

  const jobQuery = useQuery({
    queryKey: QK.kronosJob(view.jobId ?? ''),
    queryFn: ({ signal }) => api.kronosJob(view.jobId!, signal),
    enabled: activeTab === 'single' && Boolean(view.jobId),
    staleTime: 0,
    refetchOnMount: 'always',
    retry: false,
    refetchIntervalInBackground: false,
    refetchInterval: query => isKronosJobActive(query.state.data as KronosJob | undefined) ? 1200 : false,
  })

  const selection = view.selection
  const typeQuery = useQuery({
    queryKey: QK.instrumentSearch(selection?.symbol ?? '', 'stock,etf'),
    queryFn: () => api.instrumentSearch(selection!.symbol, 20, 'stock,etf'),
    enabled: Boolean(selection && !selection.assetType),
    staleTime: 5 * 60_000,
    retry: false,
  })
  const searchedAssetType = typeQuery.data?.results.find(item => item.symbol === selection?.symbol)?.asset_type
  const assetType = selection?.assetType ?? (searchedAssetType === 'stock' || searchedAssetType === 'etf' ? searchedAssetType : undefined)

  const createMutation = useMutation({
    mutationFn: (request: KronosJobRequest) => api.kronosCreateJob(request),
    retry: false,
    onSuccess: job => {
      queryClient.setQueryData(QK.kronosJob(job.id), job)
      setView(current => ({ ...current, jobId: job.id }))
      void queryClient.invalidateQueries({ queryKey: QK.kronosStatus })
    },
  })

  const cancelMutation = useMutation({
    mutationFn: (id: string) => api.kronosCancelJob(id),
    retry: false,
    onSuccess: job => {
      queryClient.setQueryData(QK.kronosJob(job.id), job)
      void queryClient.invalidateQueries({ queryKey: QK.kronosStatus })
    },
  })

  const trackedJob = jobQuery.data
  const matchingJob = Boolean(trackedJob && selection
    && trackedJob.request.symbol === selection.symbol
    && trackedJob.request.period === view.period)
  const visibleJob = matchingJob ? trackedJob : undefined
  const activeOtherJob = trackedJob && !matchingJob && isKronosJobActive(trackedJob) ? trackedJob : undefined
  const status = statusQuery.data
  const canStart = Boolean(selection && status?.ready && !status.busy
    && !isKronosJobActive(trackedJob)
    && status.models.includes(view.model) && (view.period !== '1m' || view.lookback <= 240)
    && !createMutation.isPending)

  const setParam = <K extends 'model' | 'period' | 'lookback' | 'horizon' | 'samples'>(key: K, value: KronosViewState[K]) => {
    setView(current => {
      if (key === 'period') {
        const period = value as KronosPeriod
        return { ...current, period, lookback: normalizeKronosLookback(period, current.lookback) }
      }
      return { ...current, [key]: value }
    })
    if (key === 'period' && selection) {
      setSearchParams(kronosSearchParams(selection, value as KronosPeriod), { replace: true })
    }
  }

  const handleSelect = (symbol: string, name: string, type?: 'stock' | 'etf' | 'index') => {
    const selected = {
      symbol,
      name,
      ...(type === 'stock' || type === 'etf' ? { assetType: type } : {}),
    }
    setView(current => ({ ...current, selection: selected }))
    setSearchParams(kronosSearchParams(selected, view.period), { replace: true })
  }

  const startPrediction = () => {
    if (!selection || !canStart) return
    createMutation.mutate({
      symbol: selection.symbol,
      name: selection.name,
      model: view.model,
      period: view.period,
      lookback: view.lookback,
      horizon: view.horizon,
      samples: view.samples,
    })
  }

  const titleExtra = activeTab !== 'single' ? undefined : status?.ready
    ? <span className="rounded border border-bull/30 bg-bull/10 px-1.5 py-0.5 text-[10px] text-bull">模型文件已齐</span>
    : status && <span className="rounded border border-bear/30 bg-bear/10 px-1.5 py-0.5 text-[10px] text-bear">模型未就绪</span>

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        title="K线预测"
        titleExtra={titleExtra}
        subtitle="Kronos 模型情景，仅供研究参考；当前预测不下单。"
        right={activeTab === 'single' ? (
          <button
            type="button"
            onClick={() => void statusQuery.refetch()}
            disabled={statusQuery.isFetching}
            className="inline-flex h-7 items-center gap-1 rounded border border-border/50 bg-elevated px-2 text-[10px] text-secondary hover:text-foreground disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3 w-3', statusQuery.isFetching && 'animate-spin')} />
            刷新状态
          </button>
        ) : undefined}
      />

      <main className="space-y-3 p-3 sm:p-4">
        <div role="tablist" aria-label="Kronos工作区" className="flex w-full gap-1 overflow-x-auto rounded-md border border-border/40 bg-surface/70 p-1">
          {KRONOS_TABS.map(tab => (
            <button key={tab.id} type="button" role="tab" aria-selected={activeTab === tab.id}
              onClick={() => {
                setActiveTab(tab.id)
                updateKronosWorkbenchSession({ activeTab: tab.id })
              }}
              className={cn('shrink-0 rounded px-3 py-2 text-[11px] font-medium transition-colors',
                activeTab === tab.id ? 'bg-accent text-white' : 'text-secondary hover:bg-elevated hover:text-foreground')}>
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === 'single' && <>
        <SectionCard>
          <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px]">
            <span className="inline-flex items-center gap-1 rounded bg-elevated px-2 py-1 text-secondary">
              <Cpu className="h-3 w-3 text-accent" />
              {statusQuery.isPending ? '读取推理环境…' : statusQuery.isError ? '推理环境状态读取失败' : status?.ready ? '环境文件已齐 · 目标设备 CUDA' : '环境文件未齐'}
            </span>
            {status?.busy && (
              <span className="inline-flex items-center gap-1 rounded bg-amber-500/10 px-2 py-1 text-amber-400">
                <Activity className="h-3 w-3" />
                {status.active_job_id ? '设备正在处理任务' : '设备繁忙'}
              </span>
            )}
            {status?.message && <span className="min-w-0 text-muted">{status.message}</span>}
            {statusQuery.isError && <span className="text-bear">{errorText(statusQuery.error)}</span>}
          </div>

          <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(260px,1.2fr)_repeat(6,minmax(92px,1fr))]">
            <div className="min-w-0">
              <span className="mb-1 block text-[11px] text-secondary">标的（股票 / ETF）</span>
              <StockFinancialSearch
                onSelect={handleSelect}
                assetTypes="stock,etf"
                placeholder="输入股票或 ETF 代码 / 名称"
              />
              {selection && (
                <div className="mt-1 truncate text-[10px] text-muted" title={`${selection.name} ${selection.symbol}`}>
                  当前标的：<span className="text-foreground">{selection.name}</span> <span className="font-mono">{selection.symbol}</span>
                  {assetType && <span className="ml-1 rounded bg-elevated px-1">{assetType === 'etf' ? 'ETF' : '股票'}</span>}
                </div>
              )}
            </div>

            <Field label="模型">
              <select value={view.model} onChange={event => setParam('model', event.target.value as KronosViewState['model'])} className={selectClass}>

                <option value="base">Base</option>
              </select>
            </Field>
            <Field label="周期">
              <select value={view.period} onChange={event => setParam('period', event.target.value as KronosPeriod)} className={selectClass}>
                <option value="daily">日K</option>
                <option value="1m">1分钟K</option>
              </select>
            </Field>
            <Field label="历史窗口">
              <div className="space-y-1">
                <select value={view.lookback} onChange={event => setParam('lookback', Number(event.target.value))} className={selectClass}>
                  {KRONOS_LOOKBACKS.map(value => (
                    <option key={value} value={value} disabled={view.period === '1m' && value > 240}>
                      {value}{view.period === '1m' && value > 240 ? '（分钟模式不可用）' : ''}
                    </option>
                  ))}
                </select>
                {view.period === '1m' && <span className="block text-[9px] text-muted">1 分钟模式最多支持 240 根历史 K 线。</span>}
              </div>
            </Field>
            <Field label="预测步数">
              <select value={view.horizon} onChange={event => setParam('horizon', Number(event.target.value))} className={selectClass}>
                {KRONOS_HORIZONS.map(value => <option key={value} value={value}>{value}{view.period === 'daily' ? ' 个交易日' : ' 根1分钟K'}</option>)}
              </select>
            </Field>
            <Field label="样本数">
              <select value={view.samples} onChange={event => setParam('samples', Number(event.target.value))} className={selectClass}>
                {KRONOS_SAMPLE_COUNTS.map(value => <option key={value} value={value}>{value}</option>)}
              </select>
            </Field>
            <div className="flex items-end gap-2">
              <button
                type="button"
                onClick={startPrediction}
                disabled={!canStart}
                className="inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-btn bg-accent px-3 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                title={!selection ? '请先选择标的' : !status?.ready ? '模型环境尚未就绪' : status.busy ? '设备正在处理其他任务' : undefined}
              >
                {createMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                开始预测
              </button>
              {trackedJob && isKronosJobActive(trackedJob) && (
                <button
                  type="button"
                  onClick={() => cancelMutation.mutate(trackedJob.id)}
                  disabled={cancelMutation.isPending}
                  className="inline-flex h-8 items-center justify-center gap-1 rounded border border-bear/30 bg-bear/10 px-2 text-[11px] text-bear hover:bg-bear/20 disabled:opacity-50"
                >
                  {cancelMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Square className="h-3 w-3" />}
                  取消
                </button>
              )}
            </div>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-accent/20 bg-accent/5 px-2.5 py-2 text-[11px] text-secondary">
            {view.period === 'daily' ? (
              <>
                <span>当前是日K预测：{view.lookback} 根历史日K → 未来 {view.horizon} 个交易日。盘中短线请用1分钟K。</span>
                <button type="button" onClick={() => setParam('period', '1m')} className="shrink-0 text-accent hover:underline">切换1分钟K</button>
              </>
            ) : (
              <span>当前是1分钟K预测：{view.lookback} 根历史分钟K → 后续 {view.horizon} 根1分钟K。预测接在最新已完成分钟之后，午休或收盘后顺延至下一交易时段。</span>
            )}
          </div>

          {createMutation.isError && <p className="mt-2 flex items-center gap-1 text-[11px] text-bear"><AlertCircle className="h-3 w-3" />{errorText(createMutation.error)}</p>}
          {cancelMutation.isError && <p className="mt-2 flex items-center gap-1 text-[11px] text-bear"><AlertCircle className="h-3 w-3" />取消失败：{errorText(cancelMutation.error)}</p>}
          {!statusQuery.isPending && status?.ready && !status.models.includes(view.model) && (
            <p className="mt-2 text-[11px] text-amber-400">当前环境未声明 {modelLabel(view.model)} 模型可用。</p>
          )}
        </SectionCard>

        {activeOtherJob && (
          <SectionCard className="border-amber-500/25 bg-amber-500/5">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Activity className="h-4 w-4 text-amber-400" />
              <span className="font-medium text-foreground">另一标的的预测任务仍在运行</span>
              <span className="font-mono text-muted">{activeOtherJob.request.name} {activeOtherJob.request.symbol}</span>
              <span className="text-muted">{stateLabel(activeOtherJob.state)}</span>
              <span className="text-[10px] text-muted">当前页面已隐藏该任务结果。</span>
            </div>
          </SectionCard>
        )}

        {view.jobId && jobQuery.isPending && !trackedJob && (
          <SectionCard className="flex items-center gap-2 text-xs text-secondary">
            <Loader2 className="h-4 w-4 animate-spin text-accent" />正在读取已保存的预测任务…
          </SectionCard>
        )}

        {matchingJob && visibleJob && (
          <section className="space-y-3">
            {isKronosJobActive(visibleJob) && (
              <SectionCard className="flex items-center gap-2 text-xs text-secondary">
                <Loader2 className="h-4 w-4 animate-spin text-accent" />
                {stateLabel(visibleJob.state)}：{visibleJob.message || 'Kronos 正在生成预测情景'}
                {jobQuery.isError && <span className="text-bear">状态读取失败：{errorText(jobQuery.error)}</span>}
              </SectionCard>
            )}

            {visibleJob.state === 'failed' && (
              <SectionCard className="border-bear/25 bg-bear/5">
                <div className="flex items-start gap-2 text-xs text-bear">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <div><div className="font-medium">预测任务失败</div><div className="mt-1 break-words">{visibleJob.error || visibleJob.message || '服务未返回错误说明'}</div></div>
                </div>
              </SectionCard>
            )}

            {visibleJob.state === 'cancelled' && (
              <SectionCard className="flex items-center gap-2 text-xs text-secondary">
                <Square className="h-3.5 w-3.5 text-muted" />预测任务已取消。
              </SectionCard>
            )}

            {visibleJob.state === 'complete' && visibleJob.result && (
              <CompleteResult job={visibleJob} assetType={assetType} />
            )}

            {visibleJob.state === 'complete' && !visibleJob.result && (
              <SectionCard className="flex items-center gap-2 text-xs text-secondary">
                <AlertCircle className="h-4 w-4 text-amber-400" />任务已完成，但没有可展示的结果。
              </SectionCard>
            )}
          </section>
        )}

        {jobQuery.isError && !matchingJob && !activeOtherJob && (
          <SectionCard className="flex items-start gap-2 text-xs text-bear">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            读取当前预测任务失败：{errorText(jobQuery.error)}
          </SectionCard>
        )}

        {!visibleJob && !activeOtherJob && (!view.jobId || !jobQuery.isPending) && !jobQuery.isError && (
          <SectionCard className="flex min-h-40 flex-col items-center justify-center gap-2 text-center">
            <Activity className="h-5 w-5 text-muted" />
            <div className="text-xs font-medium text-secondary">
              {selection ? '暂无与当前标的和周期匹配的预测结果' : '先搜索并选择一只股票或 ETF'}
            </div>
            <div className="max-w-lg text-[10px] leading-5 text-muted">
              选择 Base、周期与窗口后，点击“开始预测”才会提交任务。切换标的时不会展示其他标的的预测结果。
            </div>
          </SectionCard>
        )}
        </>}

        {activeTab === 'comparison' && (
          <KronosComparisonPanel
            selection={selection}
            period={view.period}
            onSelect={handleSelect}
            onPeriodChange={nextPeriod => setParam('period', nextPeriod)}
          />
        )}
        {activeTab === 'summary' && (
          <KronosSummaryPanel
            selection={selection}
            period={view.period}
            onSelect={handleSelect}
            onPeriodChange={nextPeriod => setParam('period', nextPeriod)}
          />
        )}
        {activeTab === 'candidates' && <KronosCandidatePanel />}
      </main>
    </div>
  )
}

function CompleteResult({ job, assetType }: { job: KronosJob; assetType?: KronosAssetType }) {
  const result = job.result!
  const savedParams = kronosSearchParams({ symbol: result.symbol, name: result.name,
    ...(assetType ? { assetType } : {}) }, result.period)
  savedParams.set('job', job.id)
  const hasChartData = result.history.length > 0 || result.forecast.length > 0
  const firstForecast = result.forecast[0]?.time
  const lastForecast = result.forecast[result.forecast.length - 1]?.time
  const crossSession = result.period === '1m' && firstForecast && lastForecast
    && (firstForecast.slice(0, 10) !== result.input_as_of.slice(0, 10)
      || lastForecast.slice(0, 10) !== result.input_as_of.slice(0, 10))
  return (
    <>
      <SectionCard className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex items-center gap-1 rounded bg-bull/10 px-2 py-1 text-[11px] font-medium text-bull">
            <CheckCircle2 className="h-3.5 w-3.5" />预测情景
          </span>
          <span className="text-sm font-semibold text-foreground">{result.name}</span>
          <span className="font-mono text-[11px] text-muted">{result.symbol}</span>
          <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-secondary">{modelLabel(result.model)}</span>
          <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-secondary">{periodLabel(result.period)}</span>
          <span className="text-[10px] text-muted">{job.request.lookback} 根历史 · {job.request.horizon}{result.period === 'daily' ? ' 个交易日' : ' 根1分钟K'} · {job.request.samples} 个样本</span>
          <a className="ml-auto rounded border border-border px-2 py-1 text-[10px] text-accent hover:bg-elevated"
            href={`/kronos?${savedParams}`} title="收藏此链接，可回看原预测及事后核对；不会重新预测">原预测与核对链接</a>
        </div>

        <div className="grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-3">
          <Metric label="行情截至（北京时间）" value={formatKronosTime(result.input_as_of, result.period)} />
          <Metric label="提问时间（北京时间）" value={formatKronosTime(result.requested_at || job.requested_at, '1m')} />
          <Metric label="预测开始（北京时间）" value={firstForecast ? formatKronosTime(firstForecast, result.period) : '—'} />
          <Metric label="预测结束（北京时间）" value={lastForecast ? formatKronosTime(lastForecast, result.period) : '—'} />
          <Metric label="耗时" value={`${result.metrics.elapsed_seconds.toFixed(2)} 秒`} />
          <Metric label="价格口径" value={result.price_basis} />
        </div>

        <p className="rounded border border-purple-500/25 bg-purple-500/5 px-2.5 py-2 text-[11px] text-secondary">
          {result.period === 'daily'
            ? '未来交易日预测：每根预测日K代表1个交易日，不是当日分时。'
            : crossSession
              ? '跨交易日分钟预测：后续分钟已接到下一交易日，不是重画今天已经发生的走势。'
              : '当日后续分钟预测：每根代表1分钟；午休会跳过，跨收盘会接到下一交易日。'}
        </p>

        {result.forecast_mode === 'close_only' && (
          <div role="status" className="rounded border border-amber-500/40 bg-amber-500/10 p-2.5">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-amber-300">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              {KRONOS_CLOSE_ONLY_WARNING}
            </div>
            <p className="mt-1 text-[10px] leading-4 text-secondary">
              预测开盘价、最高价和最低价不满足 K 线约束，已省略，未修正或用于绘图。
            </p>
          </div>
        )}

        {result.warnings.length > 0 && (
          <div className="rounded border border-amber-500/25 bg-amber-500/5 p-2">
            <div className="mb-1 flex items-center gap-1 text-[10px] font-medium text-amber-400"><AlertTriangle className="h-3 w-3" />模型与数据警告</div>
            <ul className="list-disc space-y-0.5 pl-4 text-[10px] leading-4 text-secondary">
              {result.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}
            </ul>
          </div>
        )}

        {hasChartData ? (
          <div>
            <div className="mb-1 flex flex-wrap items-center justify-between gap-1 text-[10px] text-muted">
              <span>{result.period === '1m'
                ? '历史分时走势 + 预测收盘价路径'
                : result.forecast_mode === 'close_only' ? '历史蜡烛K线 + 预测收盘价路径' : '历史K线 + 预测K线'} · 时间为北京时间</span>
              <span className="inline-flex items-center gap-1 text-purple-400"><span className="w-5 border-t border-dashed border-purple-400" />预测情景</span>
            </div>
            <KronosChart result={result} assetType={assetType} />
          </div>
        ) : (
          <div className="flex min-h-32 items-center justify-center rounded bg-elevated/50 text-xs text-muted">任务完成，但没有可绘制的历史或预测。</div>
        )}

        <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-border/30 pt-2 text-[10px] text-muted">
          <span>CUDA 峰值已分配：{result.metrics.cuda_peak_allocated_mb.toFixed(1)} MB</span>
          <span>CUDA 峰值保留：{result.metrics.cuda_peak_reserved_mb.toFixed(1)} MB</span>
          <span>预测不下单</span>
        </div>
      </SectionCard>
      <KronosEvaluation job={job} assetType={assetType} />
    </>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded bg-elevated/70 px-2 py-1.5">
      <div className="flex items-center gap-1 text-muted"><Clock3 className="h-3 w-3" />{label}</div>
      <div className="mt-0.5 truncate font-mono text-secondary" title={value}>{value}</div>
    </div>
  )
}
