import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Loader2, RefreshCw, Square } from 'lucide-react'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { KronosChart } from './KronosChart'
import { KronosEvaluation } from './KronosEvaluation'
import { KRONOS_HORIZONS, KRONOS_LOOKBACKS, KRONOS_SAMPLE_COUNTS, type KronosAssetType, type KronosSelection } from './state'
import { formatKronosTime } from './chartModel'
import {
  readKronosWorkbenchSession,
  updateKronosWorkbenchSession,
} from './workbenchSession'
import type { KronosModel, KronosPeriod } from './types'
import {
  effectiveKronosLookbacks,
  isKronosComparisonActive,
  type KronosCandidate,
  type KronosComparison,
  type KronosComparisonList,
  type KronosComparisonRequest,
  type KronosComparisonSummary,
  type KronosComparisonTrial,
} from './comparisonTypes'
import { kronosComparisonRefetchInterval, kronosSummaryRefetchInterval } from './polling'

const SELECT_CLASS = 'h-8 rounded border border-border bg-elevated px-2 text-xs text-foreground focus:border-accent/50 focus:outline-none'

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请稍后重试'
}

function PanelCard({ children, className }: { children: React.ReactNode; className?: string }) {
  return <section className={cn('rounded-md border border-border/40 bg-surface/70 p-3 sm:p-4', className)}>{children}</section>
}

function ScopePicker({
  selection,
  period,
  onSelect,
  onPeriodChange,
}: {
  selection: KronosSelection | null
  period: KronosPeriod
  onSelect: (symbol: string, name: string, type?: 'stock' | 'etf' | 'index') => void
  onPeriodChange: (period: KronosPeriod) => void
}) {
  return (
    <PanelCard>
      <div className="grid gap-3 sm:grid-cols-[minmax(240px,1fr)_160px] sm:items-end">
        <label className="min-w-0 text-[11px] text-secondary">
          <span className="mb-1 block">股票 / ETF</span>
          <StockFinancialSearch onSelect={onSelect} assetTypes="stock,etf" placeholder="输入股票或 ETF 代码 / 名称" />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-secondary">
          <span>预测周期</span>
          <select aria-label="比较周期" value={period} onChange={event => onPeriodChange(event.target.value as KronosPeriod)} className={SELECT_CLASS}>
            <option value="daily">日K</option>
            <option value="1m">1分钟K</option>
          </select>
        </label>
      </div>
      <p className="mt-2 text-[10px] text-muted">
        {selection ? `当前范围：${selection.name} ${selection.symbol} · ${period === 'daily' ? '日K' : '1分钟K'}` : '先选择股票或 ETF，再读取对应周期的数据。'}
      </p>
    </PanelCard>
  )
}

function StatePill({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'good' | 'warn' | 'bad' }) {
  const colors = {
    neutral: 'bg-elevated text-secondary',
    good: 'bg-bull/10 text-bull',
    warn: 'bg-amber-500/10 text-amber-300',
    bad: 'bg-bear/10 text-bear',
  }
  return <span className={cn('inline-flex rounded px-2 py-1 text-[10px]', colors[tone])}>{children}</span>
}

function comparisonStateLabel(state: KronosComparison['state']): string {
  const labels: Record<KronosComparison['state'], string> = {
    preparing: '准备中', running: '比较中', complete: '已完成', partial: '部分完成',
    failed: '失败', cancelled: '已取消', interrupted: '中断',
  }
  return labels[state]
}

function trialStateLabel(trial: KronosComparisonTrial): string {
  const labels: Record<KronosComparisonTrial['state'], string> = {
    waiting: '等待中', preparing: '准备中', running: '运行中', complete: '已完成',
    failed: '失败', cancelled: '已取消', skipped: '跳过',
  }
  return labels[trial.state]
}

function modelLabel(model: KronosModel): string {
  return model === 'small' ? 'Small' : 'Base'
}

export function kronosTimingLabel(timely: boolean | null): string {
  if (timely === true) return '时效达标'
  if (timely === false) return '迟到结果 · 非实时预测'
  return '时效未判定'
}

function formatMetric(value: number | null, digits = 6): string {
  return value === null ? '—' : value.toFixed(digits)
}

function formatPercent(value: number | null): string {
  return value === null ? '—' : `${(value * 100).toFixed(2)}%`
}

function trialTone(state: KronosComparisonTrial['state']): 'neutral' | 'good' | 'warn' | 'bad' {
  if (state === 'complete') return 'good'
  if (state === 'failed' || state === 'cancelled') return 'bad'
  if (state === 'running' || state === 'preparing') return 'warn'
  return 'neutral'
}

export function KronosComparisonPanel({
  selection,
  period,
  onSelect,
  onPeriodChange,
}: {
  selection: KronosSelection | null
  period: KronosPeriod
  onSelect: (symbol: string, name: string, type?: 'stock' | 'etf' | 'index') => void
  onPeriodChange: (period: KronosPeriod) => void
}) {
  const queryClient = useQueryClient()
  const [initialSession] = useState(readKronosWorkbenchSession)
  const scope = `${selection?.symbol ?? ''}:${period}`
  const scopeState = selection ? { symbol: selection.symbol, period } : null
  const storedScopeMatches = initialSession.scope?.symbol === selection?.symbol
    && initialSession.scope?.period === period
  const [models, setModels] = useState<KronosModel[]>(initialSession.models)
  const [lookbacks, setLookbacks] = useState<number[]>(() => effectiveKronosLookbacks(initialSession.lookbacks, period))
  const [horizon, setHorizon] = useState<5 | 10 | 20>(initialSession.horizon)
  const [samples, setSamples] = useState<1 | 3 | 5>(initialSession.samples)
  const [selectedBatch, setSelectedBatch] = useState<{ scope: string; id: string } | null>(() => (
    storedScopeMatches && initialSession.selectedBatchId
      ? { scope, id: initialSession.selectedBatchId }
      : null
  ))
  const [selectedTrial, setSelectedTrial] = useState<{ comparisonId: string; trialId: string } | null>(() => (
    storedScopeMatches && initialSession.selectedBatchId && initialSession.selectedTrialId
      ? { comparisonId: initialSession.selectedBatchId, trialId: initialSession.selectedTrialId }
      : null
  ))
  const [labelDraft, setLabelDraft] = useState<{ comparisonId: string; trialId: string; value: string } | null>(null)
  const [savedMessage, setSavedMessage] = useState('')
  const previousScope = useRef(scope)
  const previousBatchId = useRef<string | null>(null)
  const lookbackChoices: number[] = KRONOS_LOOKBACKS.filter(value => period === 'daily' || value <= 240)
  const effectiveLookbacks = effectiveKronosLookbacks(lookbacks, period)

  useEffect(() => {
    const valid = effectiveKronosLookbacks(lookbacks, period)
    if (valid.length !== lookbacks.length || valid.some((value, index) => value !== lookbacks[index])) {
      setLookbacks(valid)
      updateKronosWorkbenchSession({ lookbacks: valid })
    }
  }, [period, lookbacks])

  const comparisonsQuery = useQuery({
    queryKey: QK.kronosComparisons(selection?.symbol ?? '', period),
    queryFn: ({ signal }) => api.kronosComparisons(selection!.symbol, period, signal),
    enabled: Boolean(selection),
    staleTime: 30_000,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: false,
    refetchIntervalInBackground: false,
  })
  const comparisons = comparisonsQuery.data?.items ?? []
  const selectedBatchId = selectedBatch?.scope === scope && comparisons.some(item => item.id === selectedBatch.id)
    ? selectedBatch.id
    : comparisons[0]?.id ?? null
  useEffect(() => {
    const targetScope = selection ? { symbol: selection.symbol, period } : null
    if (previousScope.current !== scope) {
      const oldBatchId = previousBatchId.current
      if (oldBatchId) queryClient.removeQueries({ queryKey: QK.kronosComparison(oldBatchId), exact: true })
      previousScope.current = scope
      previousBatchId.current = selectedBatchId
      setSelectedBatch(null)
      setSelectedTrial(null)
      updateKronosWorkbenchSession({ scope: targetScope, selectedBatchId: null, selectedTrialId: null })
      return
    }

    previousBatchId.current = selectedBatchId
    const stored = readKronosWorkbenchSession()
    const sameScope = targetScope === null
      ? stored.scope === null
      : stored.scope?.symbol === targetScope.symbol && stored.scope.period === targetScope.period
    if (!sameScope) {
      if (stored.selectedBatchId) queryClient.removeQueries({ queryKey: QK.kronosComparison(stored.selectedBatchId), exact: true })
      updateKronosWorkbenchSession({ scope: targetScope, selectedBatchId: null, selectedTrialId: null })
    }
  }, [scope, selectedBatchId, selection?.symbol, period, queryClient])
  useEffect(() => {
    if (!selection || !comparisonsQuery.data || !selectedBatch || selectedBatch.scope !== scope) return
    if (comparisons.some(item => item.id === selectedBatch.id)) return
    setSelectedBatch(null)
    setSelectedTrial(null)
    updateKronosWorkbenchSession({ scope: scopeState, selectedBatchId: null, selectedTrialId: null })
  }, [comparisonsQuery.data, selectedBatch, scope, selection?.symbol, period])
  const detailQuery = useQuery({
    queryKey: QK.kronosComparison(selectedBatchId ?? ''),
    queryFn: ({ signal }) => api.kronosComparison(selectedBatchId!, signal),
    enabled: Boolean(selectedBatchId),
    staleTime: 15_000,
    gcTime: 120_000,
    retry: false,
    refetchOnWindowFocus: false,
    refetchIntervalInBackground: false,
    refetchInterval: query => kronosComparisonRefetchInterval(query.state.data as KronosComparison | undefined),
  })
  const comparison = detailQuery.data?.id === selectedBatchId ? detailQuery.data : undefined
  const hasActiveComparison = isKronosComparisonActive(comparison)
    || comparisons.some(item => item.id !== comparison?.id && isKronosComparisonActive(item))
  const selectedTrialId = comparison && selectedTrial?.comparisonId === comparison.id
    && comparison.trials.some(item => item.id === selectedTrial.trialId)
    ? selectedTrial.trialId
    : comparison?.trials[0]?.id
  const trial = comparison?.trials.find(item => item.id === selectedTrialId)
  const activeTrials = comparison?.trials.filter(item => ['waiting', 'preparing', 'running'].includes(item.state)).length ?? 0

  const createMutation = useMutation({
    mutationFn: (request: KronosComparisonRequest) => api.kronosCreateComparison(request),
    retry: false,
    onSuccess: created => {
      queryClient.setQueryData(QK.kronosComparison(created.id), created)
      queryClient.setQueryData<KronosComparisonList>(
        QK.kronosComparisons(created.request.symbol, created.request.period),
        previous => ({
          items: [created, ...(previous?.items ?? []).filter(item => item.id !== created.id)].slice(0, 20),
          unavailable: previous?.unavailable ?? null,
        }),
      )
      const trialId = created.trials[0]?.id ?? null
      const createdScope = `${created.request.symbol}:${created.request.period}`
      setSelectedBatch({ scope: createdScope, id: created.id })
      setSelectedTrial(trialId ? { comparisonId: created.id, trialId } : null)
      updateKronosWorkbenchSession({
        scope: { symbol: created.request.symbol, period: created.request.period },
        selectedBatchId: created.id,
        selectedTrialId: trialId,
      })
      setSavedMessage('')
    },
  })
  const cancelMutation = useMutation({
    mutationFn: (id: string) => api.kronosCancelComparison(id),
    retry: false,
    onSuccess: updated => {
      queryClient.setQueryData(QK.kronosComparison(updated.id), updated)
      void queryClient.invalidateQueries({ queryKey: QK.kronosComparisons(updated.request.symbol, updated.request.period) })
    },
  })
  const saveCandidateMutation = useMutation({
    mutationFn: (input: { comparison_id: string; trial_id: string; label: string }) => api.kronosCreateCandidate(input),
    retry: false,
    onSuccess: candidate => {
      setSavedMessage(`已登记候选配置「${candidate.label}」。未生成微调权重，也不会自动晋级。`)
      void queryClient.invalidateQueries({ queryKey: QK.kronosCandidates })
    },
  })

  const canCreate = Boolean(selection && models.length > 0 && effectiveLookbacks.length > 0
    && models.length * effectiveLookbacks.length <= 6 && !createMutation.isPending && !hasActiveComparison)
  const toggleModel = (model: KronosModel) => setModels(current => {
    const next = current.includes(model) ? current.filter(item => item !== model) : [...current, model]
    updateKronosWorkbenchSession({ models: next })
    return next
  })
  const toggleLookback = (lookback: number) => setLookbacks(current => {
    const effective = effectiveKronosLookbacks(current, period)
    const next = effective.includes(lookback)
      ? effective.filter(item => item !== lookback)
      : !lookbackChoices.includes(lookback) || effective.length >= 3
        ? effective
        : [...effective, lookback]
    updateKronosWorkbenchSession({ lookbacks: next })
    return next
  })
  const startComparison = () => {
    if (!selection || !canCreate) return
    setSavedMessage('')
    createMutation.mutate({ symbol: selection.symbol, name: selection.name, period, models,
      lookbacks: effectiveLookbacks, horizon, samples })
  }
  const defaultCandidateLabel = trial ? `${modelLabel(trial.model)} · ${period === 'daily' ? '日K' : '1分钟K'} · ${trial.lookback}` : ''
  const currentCandidateLabel = trial && comparison && labelDraft
    && comparison.id === labelDraft.comparisonId && labelDraft.trialId === trial.id
    ? labelDraft.value
    : defaultCandidateLabel

  return (
    <div className="space-y-3">
      <ScopePicker selection={selection} period={period} onSelect={onSelect} onPeriodChange={onPeriodChange} />
      <PanelCard className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-foreground">配置对比</h2>
            <p className="mt-1 text-[10px] text-muted">同一批次复用同一行情快照；最多比较 3 组配置。</p>
          </div>
          {selection && <span className="font-mono text-[10px] text-muted">{selection.symbol}</span>}
        </div>
        <div className="grid gap-3 lg:grid-cols-2">
          <fieldset className="min-w-0">
            <legend className="mb-1.5 text-[11px] text-secondary">模型</legend>
            <div className="flex flex-wrap gap-1.5">
              {(['base'] as const).map(model => (
                <button key={model} type="button" aria-pressed={models.includes(model)} onClick={() => toggleModel(model)}
                  className={cn('rounded border px-2.5 py-1.5 text-[11px]', models.includes(model) ? 'border-accent/50 bg-accent/10 text-accent' : 'border-border/50 text-secondary')}>
                  {modelLabel(model)}
                </button>
              ))}
            </div>
          </fieldset>
          <fieldset className="min-w-0">
            <legend className="mb-1.5 text-[11px] text-secondary">历史窗口</legend>
            <div className="flex flex-wrap gap-1.5">
              {lookbackChoices.map(value => (
                <button key={value} type="button" aria-pressed={effectiveLookbacks.includes(value)}
                  disabled={!effectiveLookbacks.includes(value) && effectiveLookbacks.length >= 3}
                  onClick={() => toggleLookback(value)}
                  className={cn('rounded border px-2.5 py-1.5 text-[11px] disabled:cursor-not-allowed disabled:opacity-40', effectiveLookbacks.includes(value) ? 'border-accent/50 bg-accent/10 text-accent' : 'border-border/50 text-secondary')}>
                  {value} 根
                </button>
              ))}
            </div>
          </fieldset>
          <label className="flex flex-col gap-1 text-[11px] text-secondary">
            预测步数
            <select value={horizon} onChange={event => {
              const next = Number(event.target.value) as 5 | 10 | 20
              setHorizon(next)
              updateKronosWorkbenchSession({ horizon: next })
            }} className={SELECT_CLASS}>
              {KRONOS_HORIZONS.map(value => <option key={value} value={value}>{value}{period === 'daily' ? ' 个交易日' : ' 根1分钟K'}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-secondary">
            样本数
            <select value={samples} onChange={event => {
              const next = Number(event.target.value) as 1 | 3 | 5
              setSamples(next)
              updateKronosWorkbenchSession({ samples: next })
            }} className={SELECT_CLASS}>
              {KRONOS_SAMPLE_COUNTS.map(value => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={startComparison} disabled={!canCreate}
            className="inline-flex h-8 items-center gap-1.5 rounded-btn bg-accent px-3 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40">
            {createMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Activity className="h-3.5 w-3.5" />}
            开始配置比较
          </button>
          <span className="text-[10px] text-muted">{models.length * effectiveLookbacks.length}/3 组配置 · {effectiveLookbacks.length}/3 个有效窗口 · 当前活动或待核对批次有限轮询</span>
          {hasActiveComparison && <span role="status" className="text-[10px] text-amber-300">当前标的与周期已有活动比较，完成或取消后再开始。</span>}
          {models.length === 0 && <span role="status" className="text-[10px] text-amber-300">至少选择一个模型。</span>}
        </div>
        {createMutation.isError && <p role="alert" className="text-[11px] text-bear">创建比较失败：{errorText(createMutation.error)}</p>}
      </PanelCard>

      <PanelCard className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-xs font-semibold text-foreground">历史比较批次</h3>
            <p className="mt-1 text-[10px] text-muted">列表只含批次摘要；仅请求当前选中批次详情。活动批次每 1.5 秒、待核对评价每分钟刷新；后台暂停。</p>
          </div>
          <button type="button" onClick={() => {
            void comparisonsQuery.refetch()
            if (selectedBatchId) void detailQuery.refetch()
          }} disabled={!selection || comparisonsQuery.isFetching || detailQuery.isFetching}
            className="inline-flex h-7 items-center gap-1 rounded border border-border/50 bg-elevated px-2 text-[10px] text-secondary disabled:opacity-50">
            <RefreshCw className={cn('h-3 w-3', (comparisonsQuery.isFetching || detailQuery.isFetching) && 'animate-spin')} />刷新记录
          </button>
        </div>
        {!selection && <p role="status" className="text-[11px] text-muted">选择股票或 ETF 后查看配置比较。</p>}
        {selection && comparisonsQuery.isPending && <p role="status" className="text-[11px] text-secondary">正在读取比较记录…</p>}
        {comparisonsQuery.isError && !comparisonsQuery.data && <p role="alert" className="text-[11px] text-bear">比较列表读取失败：{errorText(comparisonsQuery.error)}</p>}
        {comparisonsQuery.isError && comparisonsQuery.data && <p role="status" className="text-[10px] text-amber-300">列表刷新失败，继续显示上次结果。</p>}
        {(comparisonsQuery.data?.unavailable ?? 0) > 0 && (
          <p role="status" className="text-[10px] text-amber-300">另有 {comparisonsQuery.data?.unavailable} 个批次记录不可读取，未纳入列表。</p>
        )}
        {selection && !comparisonsQuery.isPending && !comparisonsQuery.isError && comparisons.length === 0 && (
          <p role="status" className="rounded border border-border/30 bg-elevated/40 p-3 text-[11px] text-muted">当前标的和周期还没有配置比较批次。</p>
        )}
        {comparisons.length > 0 && (
          <label className="flex flex-col gap-1 text-[10px] text-secondary sm:max-w-xl">
            选择批次
            <select aria-label="选择比较批次" value={selectedBatchId ?? ''}
              onChange={event => {
                const id = event.target.value
                setSelectedBatch({ scope, id })
                setSelectedTrial(null)
                updateKronosWorkbenchSession({ scope: scopeState, selectedBatchId: id, selectedTrialId: null })
              }} className={SELECT_CLASS}>
              {comparisons.map(item => (
                <option key={item.id} value={item.id}>
                  {formatKronosTime(item.created_at, '1m')} · {comparisonStateLabel(item.state)} · {item.request.models.map(modelLabel).join('/')} · {item.request.lookbacks.join('/')} 根
                </option>
              ))}
            </select>
          </label>
        )}
        {selectedBatchId && detailQuery.isPending && !comparison && <p role="status" className="text-[11px] text-secondary">正在读取批次和配置详情…</p>}
        {detailQuery.isError && !comparison && <p role="alert" className="text-[11px] text-bear">批次详情读取失败：{errorText(detailQuery.error)}</p>}
        {detailQuery.isError && comparison && <p role="status" className="text-[10px] text-amber-300">批次刷新失败，继续显示上次有效结果。</p>}
        {comparison && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 border-t border-border/30 pt-3">
              <StatePill tone={comparison.state === 'complete' ? 'good' : comparison.state === 'failed' ? 'bad' : isKronosComparisonActive(comparison) ? 'warn' : 'neutral'}>
                {comparisonStateLabel(comparison.state)}
              </StatePill>
              <span className="text-[10px] text-muted">更新于 {formatKronosTime(comparison.updated_at, '1m')}</span>
              {isKronosComparisonActive(comparison) && (
                <button type="button" onClick={() => cancelMutation.mutate(comparison.id)} disabled={cancelMutation.isPending}
                  className="inline-flex h-7 items-center gap-1 rounded border border-bear/30 bg-bear/10 px-2 text-[10px] text-bear disabled:opacity-50">
                  {cancelMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Square className="h-3 w-3" />}取消批次
                </button>
              )}
              {comparison.error && <span className="break-words text-[10px] text-bear">{comparison.error}</span>}
            </div>
            {activeTrials > 0 && <p role="status" className="text-[10px] text-secondary">{activeTrials} 个配置等待或正在运行；页面隐藏/后台时暂停轮询。</p>}
            {comparison.snapshot && (
              <p className="text-[10px] text-muted">
                共用快照：{comparison.snapshot.history_count} 根历史 · 行情截至 {formatKronosTime(comparison.snapshot.input_as_of, period)} · 价格口径 {comparison.snapshot.price_basis}
              </p>
            )}
            <div className="overflow-x-auto rounded border border-border/30">
              <table className="w-full min-w-[650px] border-collapse text-left text-[10px]">
                <thead className="bg-elevated/70 text-muted"><tr>
                  <th className="px-2 py-1.5 font-medium">模型</th><th className="px-2 py-1.5 font-medium">窗口</th>
                  <th className="px-2 py-1.5 font-medium">状态</th><th className="px-2 py-1.5 font-medium">时效</th>
                  <th className="px-2 py-1.5 font-medium">完成时间</th><th className="px-2 py-1.5 font-medium">试验错误</th>
                  <th className="px-2 py-1.5 font-medium">曲线</th>
                </tr></thead>
                <tbody className="divide-y divide-border/20">
                  {comparison.trials.map(item => (
                    <tr key={item.id} className={cn('text-secondary hover:bg-elevated/50', item.id === selectedTrialId && 'bg-accent/5')}>
                      <td className="px-2 py-1.5 font-medium">{modelLabel(item.model)}</td>
                      <td className="px-2 py-1.5">{item.lookback} 根</td>
                      <td className="px-2 py-1.5"><StatePill tone={trialTone(item.state)}>{trialStateLabel(item)}</StatePill></td>
                      <td className="px-2 py-1.5">{kronosTimingLabel(item.timely)}</td>
                      <td className="px-2 py-1.5">{item.completed_at ? formatKronosTime(item.completed_at, '1m') : '—'}</td>
                      <td className="max-w-[240px] truncate px-2 py-1.5 text-bear" title={item.error ?? ''}>{item.error || '—'}</td>
                      <td className="px-2 py-1.5">
                        <button type="button" aria-pressed={item.id === selectedTrialId}
                          aria-label={item.error ? `查看${modelLabel(item.model)}配置错误` : `查看${modelLabel(item.model)}窗口${item.lookback}根的曲线`}
                          onClick={() => {
                            setSelectedTrial({ comparisonId: comparison.id, trialId: item.id })
                            updateKronosWorkbenchSession({ scope: scopeState, selectedBatchId: comparison.id, selectedTrialId: item.id })
                          }}
                          className="rounded border border-accent/30 px-2 py-1 text-[10px] text-accent hover:bg-accent/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
                          {item.error ? '查看错误' : '查看曲线'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {trial && (
              <TrialDetails
                comparison={comparison}
                trial={trial}
                assetType={selection?.assetType}
                candidateLabel={currentCandidateLabel}
                onCandidateLabelChange={value => setLabelDraft({ comparisonId: comparison.id, trialId: trial.id, value })}
                onSaveCandidate={() => saveCandidateMutation.mutate({
                  comparison_id: comparison.id,
                  trial_id: trial.id,
                  label: currentCandidateLabel.trim() || defaultCandidateLabel,
                })}
                savingCandidate={saveCandidateMutation.isPending}
              />
            )}
            {cancelMutation.isError && <p role="alert" className="text-[10px] text-bear">取消批次失败：{errorText(cancelMutation.error)}</p>}
            {saveCandidateMutation.isError && <p role="alert" className="text-[10px] text-bear">登记候选配置失败：{errorText(saveCandidateMutation.error)}</p>}
            {savedMessage && <p role="status" className="text-[10px] text-bull">{savedMessage}</p>}
          </div>
        )}
      </PanelCard>
    </div>
  )
}

function TrialDetails({
  comparison,
  trial,
  assetType,
  candidateLabel,
  onCandidateLabelChange,
  onSaveCandidate,
  savingCandidate,
}: {
  comparison: KronosComparison
  trial: KronosComparisonTrial
  assetType?: KronosAssetType
  candidateLabel: string
  onCandidateLabelChange: (value: string) => void
  onSaveCandidate: () => void
  savingCandidate: boolean
}) {
  const hasError = Boolean(trial.error)
  const job = hasError ? null : trial.job
  return (
    <PanelCard className="space-y-3 bg-background/30">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-xs font-semibold text-foreground">当前配置：{modelLabel(trial.model)} · {trial.lookback} 根</h4>
          <p className="mt-1 text-[10px] text-muted">{kronosTimingLabel(trial.timely)} · {trial.job_id ? `任务 ${trial.job_id}` : '尚无任务 ID'}</p>
        </div>
        {trial.state === 'complete' && (
          <div className="flex flex-wrap items-center gap-1.5">
            <input aria-label="候选配置名称" value={candidateLabel} onChange={event => onCandidateLabelChange(event.target.value)}
              className="h-7 w-40 rounded border border-border bg-elevated px-2 text-[10px] text-foreground" />
            <button type="button" onClick={onSaveCandidate} disabled={savingCandidate || Boolean(trial.error)}
              title={trial.error ? '该配置存在错误，不能登记候选配置' : undefined}
              className="h-7 rounded border border-accent/40 bg-accent/10 px-2 text-[10px] text-accent disabled:opacity-50">
              {savingCandidate ? '登记中…' : '登记候选配置'}
            </button>
          </div>
        )}
      </div>
      {hasError ? (
        <p role="alert" className="rounded border border-bear/30 bg-bear/5 px-2.5 py-2 text-[11px] text-bear">配置错误：{trial.error}</p>
      ) : <>
      {trial.timely === false && (
        <p role="status" className="rounded border border-amber-500/30 bg-amber-500/5 px-2.5 py-2 text-[10px] text-amber-200">
          该配置标记为迟到结果；可查看历史预测，但不会标成实时预测。
        </p>
      )}
      {job?.result && (
        <div className="space-y-2">
          <p className="text-[10px] text-muted">行情截至 {formatKronosTime(job.result.input_as_of, job.result.period)} · {job.result.price_basis}</p>
          <KronosChart result={job.result} assetType={assetType} />
        </div>
      )}
      {job && trial.evaluation !== undefined && (
        <KronosEvaluation job={job} assetType={assetType} initialEvaluation={trial.evaluation} />
      )}
      {!job && <p className="text-[11px] text-muted">后端尚未返回该配置的任务详情。</p>}
      {job && !job.result && <p className="text-[11px] text-muted">该配置尚无可展示的预测图表。</p>}
      {comparison.snapshot && <p className="text-[9px] text-muted">快照指纹 {comparison.snapshot.sha256.slice(0, 12)}… · 实现指纹 {comparison.snapshot.implementation_sha256.slice(0, 12)}…</p>}
      </>}
    </PanelCard>
  )
}

export function KronosSummaryPanel({
  selection,
  period,
  onSelect,
  onPeriodChange,
}: {
  selection: KronosSelection | null
  period: KronosPeriod
  onSelect: (symbol: string, name: string, type?: 'stock' | 'etf' | 'index') => void
  onPeriodChange: (period: KronosPeriod) => void
}) {
  const query = useQuery({
    queryKey: QK.kronosSummary(selection?.symbol ?? '', period),
    queryFn: ({ signal }) => api.kronosComparisonSummary(selection!.symbol, period, signal),
    enabled: Boolean(selection),
    staleTime: 30_000,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: query => kronosSummaryRefetchInterval(query.state.data as KronosComparisonSummary | undefined),
    refetchIntervalInBackground: false,
  })
  const summary = query.data
  return (
    <div className="space-y-3">
      <ScopePicker selection={selection} period={period} onSelect={onSelect} onPeriodChange={onPeriodChange} />
      <PanelCard className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-foreground">成绩汇总</h2>
          <p className="mt-1 text-[10px] text-muted">不同周期、步数、价格口径及模型版本保持分组，不合并为单一分数。</p>
          </div>
          <button type="button" onClick={() => { void query.refetch() }} disabled={!selection || query.isFetching}
            className="inline-flex h-7 items-center gap-1 rounded border border-border/50 bg-elevated px-2 text-[10px] text-secondary disabled:opacity-50">
            <RefreshCw className={cn('h-3 w-3', query.isFetching && 'animate-spin')} />刷新汇总
          </button>
        </div>
        <div className="rounded border border-accent/20 bg-accent/5 px-2.5 py-2 text-[10px] leading-5 text-secondary">
          指标仅统计已完成成对起点。未成熟或部分完成的数据只展示覆盖和等待情况，不评冠军；存在未完整起点时每分钟刷新，后台暂停。
          方向一致率不是交易胜率；尚无已完成成对起点时显示为空，不填成零。
        </div>
        {!selection && <p role="status" className="text-[11px] text-muted">先选择股票或 ETF，再查看成绩汇总。</p>}
        {selection && query.isPending && <p role="status" className="text-[11px] text-secondary">正在读取成绩汇总…</p>}
        {query.isError && !summary && <p role="alert" className="text-[11px] text-bear">成绩汇总读取失败：{errorText(query.error)}</p>}
        {query.isError && summary && <p role="status" className="text-[10px] text-amber-300">刷新失败，继续显示上次有效成绩。</p>}
        {summary && <SummaryTable summary={summary} />}
      </PanelCard>
    </div>
  )
}

function SummaryTable({ summary }: { summary: KronosComparisonSummary }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted">
        <span>比较批次 {summary.batches}</span><span>旧单次任务 {summary.legacy_jobs}</span>
        <span>训练未开启</span><span>自动晋级未开启</span>
      </div>
      {summary.groups.length === 0 ? (
        <p role="status" className="rounded border border-border/30 bg-elevated/40 p-3 text-[11px] text-muted">暂无可汇总的配置结果。</p>
      ) : (
        <div className="overflow-x-auto rounded border border-border/30">
          <table className="w-full min-w-[1160px] border-collapse text-left text-[10px]">
            <thead className="bg-elevated/70 text-muted"><tr>
              <th className="px-2 py-1.5 font-medium">配置</th><th className="px-2 py-1.5 font-medium">批次 / 完整 / 及时</th>
              <th className="px-2 py-1.5 font-medium">覆盖</th><th className="px-2 py-1.5 font-medium">匹配 / 预期</th>
              <th className="px-2 py-1.5 font-medium">MAE</th><th className="px-2 py-1.5 font-medium">MAPE</th>
              <th className="px-2 py-1.5 font-medium">方向一致率</th><th className="px-2 py-1.5 font-medium">基准 MAE</th>
              <th className="px-2 py-1.5 font-medium">基准 skill</th><th className="px-2 py-1.5 font-medium">耗时 / 峰值</th>
            </tr></thead>
            <tbody className="divide-y divide-border/20">
              {summary.groups.map(group => {
                const incomplete = group.origins === 0 || group.complete_origins < group.origins || group.matched < group.expected
                const hasScores = group.complete_origins > 0
                return (
                  <tr key={group.id} className="text-secondary">
                    <td className="px-2 py-2">
                      <div className="font-medium">{modelLabel(group.model)} · {group.lookback} 根 · {group.period === 'daily' ? '日K' : '1分钟K'}</div>
                      <div className="mt-0.5 text-[9px] text-muted">{group.horizon} 步 · {group.samples} 样本 · 不排名</div>
                      <div className="mt-1"><StatePill tone={incomplete ? 'warn' : 'good'}>{incomplete ? '部分 / 等待' : '已完整匹配'}</StatePill></div>
                      {group.reason && <div className="mt-0.5 max-w-[210px] text-[9px] text-muted">{group.reason}</div>}
                    </td>
                    <td className="px-2 py-2">{group.origins} / {group.complete_origins} / {group.timely_origins}</td>
                    <td className="px-2 py-2">{formatPercent(group.coverage)}</td>
                    <td className="px-2 py-2">{group.matched} / {group.expected}</td>
                    <td className="px-2 py-2 font-mono">{hasScores ? formatMetric(group.mae) : '—'}</td>
                    <td className="px-2 py-2 font-mono">{hasScores ? formatPercent(group.mape) : '—'}</td>
                    <td className="px-2 py-2 font-mono">{hasScores ? formatPercent(group.direction_accuracy) : '—'}</td>
                    <td className="px-2 py-2 font-mono">{hasScores ? formatMetric(group.baseline_mae) : '—'}</td>
                    <td className="px-2 py-2 font-mono">{hasScores ? formatMetric(group.baseline_skill, 4) : '—'}</td>
                    <td className="px-2 py-2 font-mono">{hasScores ? `${formatMetric(group.latency_seconds, 2)} 秒 / ${formatMetric(group.peak_mb, 1)} MB` : '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function candidateConfig(candidate: KronosCandidate): string {
  return `${modelLabel(candidate.model)} · ${candidate.lookback} 根 · ${candidate.period === 'daily' ? '日K' : '1分钟K'} · ${candidate.horizon} 步 · ${candidate.samples} 样本`
}

export function KronosCandidatePanel() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: QK.kronosCandidates,
    queryFn: ({ signal }) => api.kronosCandidates(signal),
    staleTime: 30_000,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: false,
    refetchIntervalInBackground: false,
  })
  const retireMutation = useMutation({
    mutationFn: (id: string) => api.kronosRetireCandidate(id),
    retry: false,
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: QK.kronosCandidates }) },
  })
  const candidates = query.data
  return (
    <div className="space-y-3">
      <PanelCard>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-foreground">候选模型</h2>
            <p className="mt-1 text-[11px] text-secondary">基线模型＋候选配置。候选是已完成配置的登记，现阶段不包含微调权重。</p>
            <p className="mt-1 text-[10px] text-muted">不自动晋级，不改变默认模型或交易设置；归档只隐藏候选登记，不删除权重或历史。</p>
          </div>
          <button type="button" onClick={() => { void query.refetch() }} disabled={query.isFetching}
            className="inline-flex h-7 items-center gap-1 rounded border border-border/50 bg-elevated px-2 text-[10px] text-secondary disabled:opacity-50">
            <RefreshCw className={cn('h-3 w-3', query.isFetching && 'animate-spin')} />刷新
          </button>
        </div>
      </PanelCard>
      {query.isPending && <PanelCard><p role="status" className="text-[11px] text-secondary">正在读取基线和候选配置…</p></PanelCard>}
      {query.isError && !candidates && <PanelCard><p role="alert" className="text-[11px] text-bear">候选列表读取失败：{errorText(query.error)}</p></PanelCard>}
      {query.isError && candidates && <PanelCard><p role="status" className="text-[10px] text-amber-300">刷新失败，继续显示上次有效列表。</p></PanelCard>}
      {candidates && (
        <>
          <PanelCard className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-xs font-semibold text-foreground">基线</h3>
              <span className="text-[10px] text-muted">按返回的配置清单展示</span>
            </div>
            {candidates.baselines.length === 0 ? <p className="text-[11px] text-muted">当前没有已声明的基线配置。</p> : (
              <div className="grid gap-2 md:grid-cols-2">
                {candidates.baselines.map(baseline => (
                  <div key={baseline.id} className="min-w-0 rounded border border-border/30 bg-elevated/30 p-2.5">
                    <div className="flex flex-wrap items-center gap-2"><span className="text-xs font-medium text-foreground">{baseline.label}</span>
                      <StatePill tone={baseline.installed ? 'good' : 'warn'}>{baseline.installed ? '已安装' : '未安装'}</StatePill>
                      <StatePill>配置已声明</StatePill>
                    </div>
                    <div className="mt-1 truncate font-mono text-[9px] text-muted" title={baseline.sha256}>SHA-256 {baseline.sha256}</div>
                  </div>
                ))}
              </div>
            )}
          </PanelCard>
          <PanelCard className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-xs font-semibold text-foreground">已登记候选配置</h3>
              <span className="text-[10px] text-muted">{candidates.items.filter(item => item.state === 'registered').length} 项有效登记</span>
            </div>
            {candidates.items.length === 0 ? <p role="status" className="text-[11px] text-muted">暂无候选配置。可在配置比较中登记已完成配置。</p> : (
              <div className="space-y-2">
                {candidates.items.map(candidate => (
                  <article key={candidate.id} className="rounded border border-border/30 bg-elevated/20 p-2.5">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2"><h4 className="text-xs font-medium text-foreground">{candidate.label}</h4>
                          <StatePill tone={candidate.state === 'registered' ? 'good' : 'neutral'}>{candidate.state === 'registered' ? '已登记' : '已归档'}</StatePill>
                        </div>
                        <p className="mt-1 text-[10px] text-secondary">{candidateConfig(candidate)} · seed {candidate.seed ?? '—'}</p>
                        <p className="mt-1 text-[9px] text-muted">来源比较 {candidate.source_comparison_id} · 配置 {candidate.source_trial_id} · {formatKronosTime(candidate.created_at, '1m')}</p>
                        <p className="mt-1 truncate font-mono text-[9px] text-muted" title={candidate.model_sha256}>模型 {candidate.model_sha256.slice(0, 16)}… · tokenizer {candidate.tokenizer_sha256.slice(0, 16)}…</p>
                      </div>
                      {candidate.state === 'registered' && (
                        <button type="button" onClick={() => retireMutation.mutate(candidate.id)} disabled={retireMutation.isPending}
                          className="h-7 shrink-0 rounded border border-border/50 px-2 text-[10px] text-secondary hover:bg-elevated disabled:opacity-50">
                          {retireMutation.isPending ? '归档中…' : '归档'}
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
            {retireMutation.isError && <p role="alert" className="text-[10px] text-bear">归档失败：{errorText(retireMutation.error)}</p>}
          </PanelCard>
        </>
      )}
    </div>
  )
}
