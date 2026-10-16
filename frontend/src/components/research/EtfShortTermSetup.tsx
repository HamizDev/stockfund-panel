import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  api,
  type PaperEtfShortTermBudget,
  type PaperEtfShortTermMode,
  type PaperEtfShortTermModeId,
  type PaperEtfShortTermPlan,
  type PaperEtfShortTermPositionCount,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'

interface SetupTarget {
  mode: PaperEtfShortTermMode
  initial_cash: PaperEtfShortTermBudget
  max_positions: PaperEtfShortTermPositionCount
}

interface SetupFailure {
  target: SetupTarget
  message: string
}

interface SetupSuccess {
  target: SetupTarget
  accountId: string
  created: boolean
}

interface SetupProgress {
  label: string
  total: number
  completed: SetupSuccess[]
  failed: SetupFailure[]
  active: SetupTarget | null
}

const BUDGETS: PaperEtfShortTermBudget[] = [5000, 10000, 20000, 100000]
const FOUR_ACCOUNT_BUDGETS: PaperEtfShortTermBudget[] = [5000, 20000]
const POSITION_COUNTS: PaperEtfShortTermPositionCount[] = [1, 2, 3]

function fmtMoney(value: number): string {
  return `¥${value.toLocaleString('zh-CN')}`
}

function budgetCap(plan: PaperEtfShortTermPlan, budget: PaperEtfShortTermBudget): PaperEtfShortTermPositionCount {
  const configured = plan.max_positions_by_budget[String(budget) as '5000' | '10000' | '20000' | '100000']
  return Math.min(3, Math.max(1, configured)) as PaperEtfShortTermPositionCount
}

function targetKey(target: SetupTarget): string {
  return `${target.mode.id}:${target.initial_cash}:${target.max_positions}`
}

function targetLabel(target: SetupTarget): string {
  return `${target.mode.name} · ${fmtMoney(target.initial_cash)} · 最多 ${target.max_positions} 只`
}

export function EtfShortTermSetup({ onSelect }: { onSelect: (accountId: string) => void }) {
  const qc = useQueryClient()
  const planQ = useQuery({
    queryKey: [...QK.paperAll, 'etf-shortterm-plan'],
    queryFn: api.paperEtfShortTermPlan,
    staleTime: 5 * 60_000,
    refetchInterval: 5_000,
  })
  const [modeId, setModeId] = useState<PaperEtfShortTermModeId | ''>('')
  const [budget, setBudget] = useState<PaperEtfShortTermBudget>(5000)
  const [maxPositions, setMaxPositions] = useState<PaperEtfShortTermPositionCount>(3)
  const [progress, setProgress] = useState<SetupProgress | null>(null)
  const [busy, setBusy] = useState(false)

  const plan = planQ.data
  const budgets = plan?.budgets.filter(value => BUDGETS.includes(value)) ?? []
  const fourAccountBudgets = FOUR_ACCOUNT_BUDGETS.filter(value => budgets.includes(value))
  const modes = plan?.modes ?? []
  const activeBudget = budgets.includes(budget) ? budget : budgets[0]
  const activeMode = modes.find(mode => mode.id === modeId) ?? modes[0]
  const maxForBudget = plan && activeBudget ? budgetCap(plan, activeBudget) : 1
  const activeMaxPositions = Math.min(maxPositions, maxForBudget) as PaperEtfShortTermPositionCount
  const runtime = plan?.runtime
  const minuteError = runtime?.minute_error?.trim() ?? ''
  const waitReason = !runtime
    ? '运行状态暂未返回；服务状态和分钟数据仍在等待同步。'
    : minuteError
      ? `分钟行情获取失败：${minuteError}。短线模拟会等待有效分钟数据。`
      : runtime.status === '短线服务不可用'
        ? '短线服务不可用，当前无法获取分钟行情或模拟成交。'
        : runtime.status === '等待启用账户'
          ? '当前没有已启用的 ETF 短线账户；启用后服务才会拉取对应分钟行情。'
          : runtime.status === '等待行情服务/交易时段'
            ? '行情服务未就绪或当前不在连续竞价时段，分钟行情暂时等待中。'
            : (runtime.minute_rows ?? 0) <= 0
              ? '尚未收到有效分钟 K 线；新单会等待有效数据后再模拟。'
              : `已收到 ${runtime.minute_rows} 条分钟数据；交易仍按下一笔有效行情模拟。`

  const createAccounts = async (targets: SetupTarget[], label: string, retry = false) => {
    if (busy || targets.length === 0) return
    setBusy(true)
    setProgress(previous => retry && previous
      ? { ...previous, failed: [], active: null }
      : { label, total: targets.length, completed: [], failed: [], active: null })

    let selectedAccount = false
    let didComplete = false
    for (const target of targets) {
      setProgress(previous => previous ? { ...previous, active: target } : previous)
      try {
        const result = await api.paperCreateEtfShortTermAccount({
          mode: target.mode.id,
          initial_cash: target.initial_cash,
          max_positions: target.max_positions,
        })
        setProgress(previous => previous ? {
          ...previous,
          completed: [...previous.completed.filter(item => targetKey(item.target) !== targetKey(target)), {
            target,
            accountId: result.account.id,
            created: result.created,
          }],
          failed: previous.failed.filter(item => targetKey(item.target) !== targetKey(target)),
        } : previous)
        didComplete = true
        if (!selectedAccount) {
          onSelect(result.account.id)
          selectedAccount = true
        }
      } catch (cause) {
        const message = cause instanceof Error && cause.message ? cause.message : '创建 ETF 短线模拟账户失败'
        setProgress(previous => previous ? {
          ...previous,
          failed: [...previous.failed.filter(item => targetKey(item.target) !== targetKey(target)), { target, message }],
        } : previous)
      }
    }
    setProgress(previous => previous ? { ...previous, active: null } : previous)
    if (didComplete) void qc.invalidateQueries({ queryKey: QK.paperAll })
    setBusy(false)
  }

  const createSingle = () => {
    if (!plan || !activeMode || !activeBudget) return
    void createAccounts([{
      mode: activeMode,
      initial_cash: activeBudget,
      max_positions: activeMaxPositions,
    }], `${activeMode.name} · ${fmtMoney(activeBudget)}`)
  }

  const createFourAccounts = () => {
    if (!plan || modes.length !== 2 || fourAccountBudgets.length !== FOUR_ACCOUNT_BUDGETS.length) return
    const targets = modes.flatMap(mode => fourAccountBudgets.map(initial_cash => ({
      mode,
      initial_cash,
      max_positions: Math.min(maxPositions, budgetCap(plan, initial_cash)) as PaperEtfShortTermPositionCount,
    })))
    if (targets.length === 4) void createAccounts(targets, '两种短线模式 · 两档本金四账户')
  }

  const retryFailed = () => {
    if (!progress?.failed.length) return
    void createAccounts(progress.failed.map(item => item.target), progress.label, true)
  }

  return (
    <section aria-label="ETF 短线模拟" className="mb-4 min-w-0 max-w-full rounded-card border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-foreground">ETF 短线模拟</h3>
          <p className="mt-1 text-[11px] leading-5 text-muted">
            从人工预定的 15 只 ETF 池中按规则选择标的；可准备 T+0 日内波段或股票 ETF 底仓做 T 账户。
          </p>
        </div>
        <span className="shrink-0 rounded-full border border-accent/30 bg-accent/10 px-2.5 py-1 text-[10px] text-accent">新账户默认暂停</span>
      </div>

      {planQ.isLoading ? (
        <div className="mt-3 rounded-btn border border-dashed border-border p-5 text-center text-xs text-muted" role="status">正在读取 ETF 短线方案…</div>
      ) : planQ.isError ? (
        <div className="mt-3 rounded-btn border border-danger/30 bg-danger/5 p-3 text-xs text-danger" role="alert">
          ETF 短线方案读取失败。<button type="button" onClick={() => void planQ.refetch()} className="ml-1 underline underline-offset-2">重试</button>
        </div>
      ) : !plan || modes.length === 0 || budgets.length === 0 ? (
        <div className="mt-3 rounded-btn border border-dashed border-border p-4 text-center text-xs text-muted">暂无可用的 ETF 短线模拟方案。</div>
      ) : (
        <>
          <div className="mt-3 grid min-w-0 gap-2 sm:grid-cols-2 xl:grid-cols-4">
            <div className="min-w-0 rounded-btn border border-border bg-base/40 p-2.5">
              <div className="text-xs font-semibold text-foreground">初始资金</div>
              <div className="mt-1 text-[11px] leading-5 text-secondary">{budgets.map(fmtMoney).join(' / ')}</div>
            </div>
            <div className="min-w-0 rounded-btn border border-border bg-base/40 p-2.5">
              <div className="text-xs font-semibold text-foreground">总持仓预算</div>
              <div className="mt-1 text-[11px] leading-5 text-secondary">本金的 80%</div>
            </div>
            <div className="min-w-0 rounded-btn border border-border bg-base/40 p-2.5">
              <div className="text-xs font-semibold text-foreground">佣金与滑点</div>
              <div className="mt-1 text-[11px] leading-5 text-secondary">
                {(plan.commission_pct * 100).toFixed(2)}% · 最低 {fmtMoney(plan.min_commission)} · 滑点 {plan.slippage_bps}bps
              </div>
            </div>
            <div className="min-w-0 rounded-btn border border-warning/30 bg-warning/5 p-2.5">
              <div className="text-xs font-semibold text-warning">小额往返成本</div>
              <div className="mt-1 text-[11px] leading-5 text-secondary">¥2,000 一轮至少 ¥10 佣金（0.5%），另计滑点。</div>
            </div>
          </div>

          <div className="mt-2 min-w-0 rounded-btn border border-border/70 bg-base/40 p-3" aria-live="polite">
            <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
              <span className="font-medium text-foreground">短线运行状态：{runtime?.status ?? '状态同步中'}</span>
              <span className="min-w-0 break-words text-secondary">分钟源：{runtime?.minute_provider || '尚未获取'}</span>
              <span className="text-secondary">分钟数据：{runtime?.minute_rows == null ? '等待首次检查' : `${runtime.minute_rows} 条`}</span>
              {runtime?.last_fetch && <span className="text-muted">最近检查：{runtime.last_fetch.replace('T', ' ').slice(0, 19)}</span>}
            </div>
            <p className={`mt-1 break-words text-[11px] leading-5 ${minuteError ? 'text-danger' : 'text-muted'}`} role={minuteError ? 'alert' : 'status'}>
              {waitReason}
            </p>
          </div>

          <div className="mt-3 grid min-w-0 gap-2 md:grid-cols-2">
            {modes.map(mode => {
              const selected = activeMode?.id === mode.id
              return (
                <button
                  key={mode.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => setModeId(mode.id)}
                  className={`min-w-0 rounded-btn border p-3 text-left transition-colors ${selected ? 'border-accent/50 bg-accent/10' : 'border-border bg-base/40 hover:border-accent/30'}`}
                >
                  <span className="block truncate text-xs font-semibold text-foreground">{mode.name}</span>
                  <span className="mt-1 block text-[11px] leading-5 text-secondary">{mode.description}</span>
                  <span className="mt-1 block break-words text-[10px] leading-4 text-muted">候选标的：{mode.symbols.map(symbol => plan.pool?.find(item => item.symbol === symbol)?.name ?? symbol).join(' · ') || '按规则选择'}</span>
                </button>
              )
            })}
          </div>

          <div className="mt-3 grid min-w-0 gap-3 rounded-btn border border-border/70 bg-base/50 p-3 sm:grid-cols-2">
            <fieldset className="min-w-0">
              <legend className="mb-1 text-[11px] font-medium text-secondary">初始资金</legend>
              <div className="flex flex-wrap gap-2">
                {budgets.map(value => (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={activeBudget === value}
                    onClick={() => {
                      setBudget(value)
                      setMaxPositions(current => Math.min(current, budgetCap(plan, value)) as PaperEtfShortTermPositionCount)
                    }}
                    className={`rounded-btn border px-3 py-1.5 text-xs transition-colors ${activeBudget === value ? 'border-accent/50 bg-accent/10 text-accent' : 'border-border text-secondary hover:border-accent/30'}`}
                  >
                    {fmtMoney(value)}
                  </button>
                ))}
              </div>
            </fieldset>
            <fieldset className="min-w-0">
              <legend className="mb-1 text-[11px] font-medium text-secondary">单账户最多持仓</legend>
              <div className="flex flex-wrap gap-2">
                {POSITION_COUNTS.map(count => {
                  const allowed = count <= maxForBudget
                  return (
                    <button
                      key={count}
                      type="button"
                      aria-pressed={activeMaxPositions === count}
                      disabled={!allowed}
                      onClick={() => setMaxPositions(count)}
                      className={`rounded-btn border px-3 py-1.5 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-35 ${activeMaxPositions === count ? 'border-accent/50 bg-accent/10 text-accent' : 'border-border text-secondary hover:border-accent/30'}`}
                    >
                      {count} 只{allowed ? '' : ' · 超出本金上限'}
                    </button>
                  )
                })}
              </div>
              <p className="mt-1 text-[10px] leading-4 text-muted">{budgets.map(value => `${fmtMoney(value)} 档最多 ${budgetCap(plan, value)} 只`).join('；')}。</p>
            </fieldset>
          </div>

          {activeMode && activeBudget && <div className="mt-2 rounded-btn border border-border bg-base/30 p-3 text-[11px] leading-5 text-muted">
            <div>每只预算上限 {fmtMoney(activeBudget * .8 / activeMaxPositions)}（含费用）。最多持仓数不要求买满；买不起一手或信号不足就保留现金。</div>
            <div className="mt-1 flex flex-wrap gap-1">
              {plan.pool?.filter(item => activeMode.symbols.includes(item.symbol)).map(item => <span key={item.symbol} className="rounded border border-border/60 px-2 py-0.5" title={`${item.symbol} · 参考日期 ${item.as_of ?? '未知'}`}>
                {item.name} · {item.lot_cost == null ? '一手成本待核对' : `一手约${fmtMoney(item.lot_cost)}`}
                {item.lot_cost != null && item.lot_cost > activeBudget * .8 / activeMaxPositions ? ' · 超预算' : ''}
              </span>)}
            </div>
            <div>一手成本按最近原始日线估算，实际建单重新检查新鲜行情、滑点和最低佣金。</div>
          </div>}

          <div className="mt-3 rounded-btn border border-border bg-base/30 p-3">
            <h4 className="text-xs font-medium text-foreground">模拟撮合与策略边界</h4>
            <ul className="mt-1 grid min-w-0 gap-x-4 gap-y-1 text-[11px] leading-5 text-muted md:grid-cols-2">
              <li>· T+1 底仓做 T 只能卖出账户已有持仓；新买入的 ETF 当日不能卖出。</li>
              <li>· 按下一笔有效行情模拟；行情迟延或缺失时会等待，不代表一定成交。</li>
              <li>· T+0 模式可在日内买入和卖出；黄金 ETF 同类持仓不会被强制重复买入。</li>
              <li>· 新账户从创建后开始记录成交，不导入历史回测成交；模拟结果不保证成交或收益。</li>
            </ul>
            {plan.rules.length > 0 && (
              <ul className="mt-2 space-y-1 border-t border-border/50 pt-2 text-[11px] leading-5 text-muted">
                {plan.rules.map((rule, index) => <li key={`${index}-${rule}`}>· {rule}</li>)}
              </ul>
            )}
          </div>

          <div className="mt-3 flex min-w-0 flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={createSingle}
              disabled={busy || !activeMode || !activeBudget}
              className="rounded-btn bg-accent px-3 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              创建暂停账户 · {activeMode?.name ?? '选择模式'} · {activeBudget ? fmtMoney(activeBudget) : '本金不可用'} · {activeMaxPositions} 只
            </button>
            <button
              type="button"
              onClick={createFourAccounts}
              disabled={busy || modes.length !== 2 || fourAccountBudgets.length !== FOUR_ACCOUNT_BUDGETS.length}
              className="rounded-btn border border-border px-3 py-2 text-xs text-secondary hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
            >
              准备四账户（两模式 × 两档资金）
            </button>
            <span className="min-w-0 text-[10px] leading-4 text-muted">只创建，不自动启用；启停请使用下方策略账户对比表管理。</span>
          </div>
          <p className="mt-2 text-[10px] leading-5 text-muted">
            四账户会为每档本金应用对应持仓上限；重复准备会返回原账户并保留原有启停状态。
          </p>
        </>
      )}

      {progress && (
        <div className="mt-3 min-w-0 rounded-btn border border-border bg-base/40 p-3" aria-live="polite">
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
            <div className="min-w-0 break-words text-xs font-medium text-foreground">{progress.label}</div>
            <div className="shrink-0 text-[11px] text-secondary">
              成功 {progress.completed.length} / {progress.total}{progress.active ? ` · 正在准备 ${targetLabel(progress.active)}` : busy ? ' · 正在处理' : ''}
            </div>
          </div>
          {progress.completed.length > 0 && (
            <ul className="mt-2 min-w-0 space-y-1 text-[11px] leading-5 text-success">
              {progress.completed.map((item, index) => (
                <li key={`${targetKey(item.target)}-${index}`} className="break-words">
                  {item.created ? '已新建' : '已返回原账户（状态保留）'}：{targetLabel(item.target)} · {item.accountId}
                </li>
              ))}
            </ul>
          )}
          {progress.failed.length > 0 && (
            <div className="mt-2 space-y-2">
              {progress.failed.map(item => (
                <div key={targetKey(item.target)} className="break-words text-[11px] leading-5 text-danger" role="alert">
                  {targetLabel(item.target)}：{item.message}
                </div>
              ))}
              <button type="button" onClick={retryFailed} disabled={busy} className="rounded-btn border border-danger/30 px-2.5 py-1 text-[11px] text-danger disabled:opacity-40">
                重试失败项（{progress.failed.length}）
              </button>
            </div>
          )}
          {!busy && progress.failed.length === 0 && progress.completed.length === progress.total && (
            <p className="mt-2 text-[11px] text-success">账户已就绪；新账户默认暂停，重复准备时保留已有账户状态。</p>
          )}
        </div>
      )}
    </section>
  )
}
