import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type PaperEtfFamily, type PaperEtfPlan, type PaperEtfPoolItem, type PaperEtfStrategy } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { researchLink } from './researchContext'

type EtfBudget = 5000 | 20000

interface SetupTarget {
  family: PaperEtfFamily
  initial_cash: EtfBudget
  strategyName: string
}

interface SetupFailure {
  target: SetupTarget
  message: string
}

interface SetupProgress {
  label: string
  total: number
  completed: Array<{ target: SetupTarget; accountId: string }>
  failed: SetupFailure[]
  active: SetupTarget | null
}

const CATEGORIES: PaperEtfPoolItem['category'][] = ['宽基', '行业', '债券', '黄金']
const BUDGETS: EtfBudget[] = [5000, 20000]

function fmtMoney(value: number | null | undefined, digits = 0): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '待行情'
  return `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

function fmtDate(value: string | null): string {
  return value ? value.slice(0, 10) : '日期待定'
}

function planBudgets(plan: PaperEtfPlan): EtfBudget[] {
  return BUDGETS.filter(budget => plan.budgets.includes(budget))
}

function targetKey(target: SetupTarget): string {
  return `${target.family}:${target.initial_cash}`
}

function selectedStrategy(plan: PaperEtfPlan, strategyId: string): PaperEtfStrategy | undefined {
  return plan.strategies.find(strategy => strategy.strategy_id === strategyId)
}

export function EtfPaperSetup({ onSelect }: { onSelect: (accountId: string) => void }) {
  const qc = useQueryClient()
  const planQ = useQuery({
    // paperAll is the existing account-independent cache root and is invalidated after account creation.
    queryKey: [...QK.paperAll, 'etf-plan'],
    queryFn: api.paperEtfPlan,
    staleTime: 5 * 60_000,
  })
  const [strategyId, setStrategyId] = useState('')
  const [budget, setBudget] = useState<EtfBudget>(5000)
  const [progress, setProgress] = useState<SetupProgress | null>(null)
  const [busy, setBusy] = useState(false)

  const plan = planQ.data
  const budgets = plan ? planBudgets(plan) : []
  const currentStrategy = plan
    ? selectedStrategy(plan, strategyId) ?? plan.strategies.find(strategy => strategy.family === 'core') ?? plan.strategies[0]
    : undefined
  const activeBudget = budgets.includes(budget) ? budget : budgets[0]
  const pool = plan?.pool ?? []
  const unavailableBonds = pool.filter(item => item.category === '债券' && item.lot_cost != null && !item.affordable_budgets.includes(5000))

  const createAccounts = async (targets: SetupTarget[], label: string, retry = false) => {
    if (busy || targets.length === 0) return
    setBusy(true)
    setProgress(previous => retry && previous
      ? { ...previous, failed: [], active: null }
      : { label, total: targets.length, completed: [], failed: [], active: null })

    let selectedAccount = false
    let didPrepareAccount = false
    for (const target of targets) {
      setProgress(previous => previous ? { ...previous, active: target } : previous)
      try {
        const result = await api.paperCreateEtfPlanAccount({
          family: target.family,
          initial_cash: target.initial_cash,
        })
        setProgress(previous => previous ? {
          ...previous,
          completed: [...previous.completed, { target, accountId: result.account.id }],
          failed: previous.failed.filter(item => targetKey(item.target) !== targetKey(target)),
        } : previous)
        didPrepareAccount = true
        if (!selectedAccount) {
          onSelect(result.account.id)
          selectedAccount = true
        }
      } catch (cause) {
        const message = cause instanceof Error && cause.message ? cause.message : '创建 ETF 模拟账户失败'
        setProgress(previous => previous ? {
          ...previous,
          failed: [...previous.failed.filter(item => targetKey(item.target) !== targetKey(target)), { target, message }],
        } : previous)
      }
    }
    setProgress(previous => previous ? { ...previous, active: null } : previous)
    if (didPrepareAccount) void qc.invalidateQueries({ queryKey: QK.paperAll })
    setBusy(false)
  }

  const makeTarget = (family: PaperEtfFamily, initial_cash: EtfBudget, strategyName: string): SetupTarget => ({
    family,
    initial_cash,
    strategyName,
  })

  const createSingle = () => {
    if (!currentStrategy || !activeBudget) return
    void createAccounts(
      [makeTarget(currentStrategy.family, activeBudget, currentStrategy.name)],
      `${currentStrategy.name} · ${fmtMoney(activeBudget)}`,
    )
  }

  const createTwoBudgetComparison = () => {
    if (!currentStrategy || budgets.length !== 2) return
    void createAccounts(
      budgets.map(value => makeTarget(currentStrategy.family, value, currentStrategy.name)),
      `${currentStrategy.name} · 两档本金对照`,
    )
  }

  const createFourAccountComparison = () => {
    if (!plan || budgets.length !== 2) return
    const families: PaperEtfFamily[] = ['core', 'sector']
    const targets = families.flatMap(family => {
      const strategy = plan.strategies.find(item => item.family === family)
      return strategy ? budgets.map(value => makeTarget(family, value, strategy.name)) : []
    })
    if (targets.length === 4) void createAccounts(targets, '两种策略 · 四账户对照')
  }

  const retryFailed = () => {
    if (!progress?.failed.length) return
    void createAccounts(progress.failed.map(item => item.target), progress.label, true)
  }

  return (
    <section aria-label="ETF 模拟入门" className="mt-4 min-w-0 rounded-card border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-foreground">ETF 模拟入门</h3>
          <p className="mt-1 text-[11px] leading-5 text-muted">
            宽基配置池覆盖债券与黄金，行业策略单独记账。每个账户最多持有 1 只 ETF；按 80% 本金入场，信号在下一交易日开盘模拟。
          </p>
        </div>
        <span className="shrink-0 rounded-full border border-accent/30 bg-accent/10 px-2.5 py-1 text-[10px] text-accent">默认暂停</span>
      </div>

      <div className="mt-3 grid gap-2 rounded-btn border border-border/70 bg-base/50 p-3 sm:grid-cols-2 xl:grid-cols-4">
        {BUDGETS.map(value => (
          <div key={value} className="rounded-btn border border-border bg-surface/60 p-2.5">
            <div className="text-xs font-semibold text-foreground">本金 {fmtMoney(value)}</div>
            <div className="mt-1 text-[11px] text-secondary">80% 入场预算 {fmtMoney(value * (plan?.entry_pct ?? 80) / 100)}</div>
          </div>
        ))}
        <div className="rounded-btn border border-border bg-surface/60 p-2.5">
          <div className="text-xs font-semibold text-foreground">单边佣金 {((plan?.commission_pct ?? 0.0005) * 100).toFixed(2)}%</div>
          <div className="mt-1 text-[11px] text-secondary">最低 {fmtMoney(plan?.min_commission ?? 5)} · 印花税 0 · 滑点 {plan?.slippage_bps ?? 5}bps</div>
        </div>
        <div className="rounded-btn border border-border bg-surface/60 p-2.5">
          <div className="text-xs font-semibold text-foreground">最多持仓 {plan?.max_positions ?? 1} 只</div>
          <div className="mt-1 text-[11px] text-secondary">每个预算、每种策略各自独立账户</div>
        </div>
      </div>

      <p className="mt-2 text-[11px] leading-5 text-muted">
        最低 5 元已用于模拟成交；历史回测按比例费用，需核对小额成本。创建账户不会下单或自动启用；重复准备会返回已有账户，保留其资金与启用状态。
      </p>
      <p className="mt-1 text-[11px] leading-5 text-muted">
        分红与份额折算数据尚未全部补齐，历史收益需另行核对。新账户从零开始记录后续成交，不导入历史回测交易；模拟统计不等于券商收益或真实交易。
      </p>

      {planQ.isLoading ? (
        <div className="mt-3 rounded-btn border border-dashed border-border p-5 text-center text-xs text-muted" role="status">正在读取 ETF 候选与一手成本…</div>
      ) : planQ.isError ? (
        <div className="mt-3 rounded-btn border border-danger/30 bg-danger/5 p-3 text-xs text-danger" role="alert">
          ETF 模拟方案读取失败。<button type="button" onClick={() => void planQ.refetch()} className="ml-1 underline underline-offset-2">重试</button>
        </div>
      ) : !plan || plan.strategies.length === 0 ? (
        <div className="mt-3 rounded-btn border border-dashed border-border p-4 text-center text-xs text-muted">暂无可用的 ETF 模拟策略。</div>
      ) : (
        <>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {plan.strategies.map(strategy => {
              const selected = currentStrategy?.strategy_id === strategy.strategy_id
              return (
                <div
                  key={strategy.strategy_id}
                  className={`min-w-0 rounded-btn border p-3 transition-colors ${selected ? 'border-accent/50 bg-accent/10' : 'border-border bg-base/40'}`}
                >
                  <span className="flex flex-wrap items-center justify-between gap-2">
                    <button
                      type="button"
                      aria-pressed={selected}
                      onClick={() => setStrategyId(strategy.strategy_id)}
                      className="min-w-0 text-left"
                    >
                      <span className="block truncate text-xs font-semibold text-foreground">{strategy.name}</span>
                      <span className="mt-0.5 block font-mono text-[10px] text-muted">{strategy.strategy_id} · {strategy.family === 'core' ? '宽基配置池' : '行业趋势池'}</span>
                    </button>
                    <Link
                      to={researchLink('backtest', strategy.strategy_id, 'etf')}
                      className="shrink-0 text-[11px] text-accent hover:underline"
                    >历史回测 →</Link>
                  </span>
                  <span className="mt-2 block text-[11px] leading-5 text-secondary">{strategy.description}</span>
                  <span className="mt-1 block truncate font-mono text-[10px] text-muted" title={strategy.symbols.join(' · ')}>
                    候选 {strategy.symbols.join(' · ') || '待更新'}
                  </span>
                </div>
              )
            })}
          </div>

          <fieldset className="mt-3 min-w-0">
            <legend className="mb-1 text-[11px] font-medium text-secondary">选择初始本金</legend>
            <div className="flex flex-wrap gap-2">
              {BUDGETS.map(value => {
                const available = budgets.includes(value)
                return (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={activeBudget === value}
                    disabled={!available}
                    onClick={() => setBudget(value)}
                    className={`rounded-btn border px-3 py-1.5 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${activeBudget === value ? 'border-accent/50 bg-accent/10 text-accent' : 'border-border text-secondary hover:border-accent/30'}`}
                  >
                    {fmtMoney(value)} · 入场 {fmtMoney(value * (plan.entry_pct / 100))}
                  </button>
                )
              })}
            </div>
          </fieldset>

          <div className="mt-3 rounded-btn border border-border bg-base/30 p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h4 className="text-xs font-medium text-foreground">ETF 候选池与一手成本</h4>
                <p className="mt-0.5 text-[10px] leading-4 text-muted">¥5,000 档按一手成本标示债券 ETF 是否可买；实际可买数量还受入场预算、滑点与成交价格影响。</p>
              </div>
              <span className="text-[10px] text-muted">当前预算 {activeBudget ? fmtMoney(activeBudget) : '不可用'}</span>
            </div>
            {unavailableBonds.length > 0 && (
              <p className="mt-2 rounded-btn border border-warning/30 bg-warning/5 px-2.5 py-2 text-[11px] leading-5 text-warning">
                ¥5,000 档买不了以下债券 ETF 的一手：{unavailableBonds.map(item => item.symbol).join('、')}。每只标的按下方预算可买提示判断。
              </p>
            )}
            {pool.length === 0 ? (
              <div className="mt-2 py-3 text-center text-xs text-muted">暂无候选数据。</div>
            ) : (
              <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                {CATEGORIES.map(category => {
                  const items = pool.filter(item => item.category === category)
                  return (
                    <div key={category} className="min-w-0 rounded-btn border border-border/70 bg-surface/50 p-2">
                      <div className="mb-1 text-[11px] font-medium text-secondary">{category} · {items.length}</div>
                      {items.length === 0 ? <p className="py-1 text-[10px] text-muted">暂无</p> : (
                        <ul className="space-y-1.5">
                          {items.map(item => (
                            <li key={item.symbol} className="min-w-0 border-t border-border/40 pt-1.5 first:border-0 first:pt-0">
                              <div className="flex min-w-0 items-center justify-between gap-1 text-[10px]">
                                <span className="truncate text-foreground" title={item.name}>{item.name}</span>
                                <span className="shrink-0 font-mono text-muted">{item.symbol}</span>
                              </div>
                              <div className="mt-0.5 text-[10px] text-muted">一手 {fmtMoney(item.lot_cost, 2)} · 收盘 {fmtMoney(item.last_close, 3)} · {fmtDate(item.as_of)}</div>
                              <div className="mt-0.5 flex flex-wrap gap-1">
                                {BUDGETS.map(value => (
                                  <span key={value} className={`rounded px-1 py-0.5 text-[9px] ${item.affordable_budgets.includes(value) ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning'}`}>
                                    ¥{(value / 1000).toFixed(0)}k {item.lot_cost == null ? '待行情' : item.affordable_budgets.includes(value) ? '可买' : '不可买'}
                                  </span>
                                ))}
                              </div>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={createSingle}
              disabled={busy || !currentStrategy || !activeBudget}
              className="rounded-btn bg-accent px-3 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              创建暂停账户 · {currentStrategy?.name ?? '选择策略'} · {activeBudget ? fmtMoney(activeBudget) : '本金不可用'}
            </button>
            <button
              type="button"
              onClick={createTwoBudgetComparison}
              disabled={busy || !currentStrategy || budgets.length !== 2}
              className="rounded-btn border border-border px-3 py-2 text-xs text-secondary hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
            >准备两档对照账户</button>
            <button
              type="button"
              onClick={createFourAccountComparison}
              disabled={busy || budgets.length !== 2 || !['core', 'sector'].every(family => plan.strategies.some(strategy => strategy.family === family))}
              className="rounded-btn border border-border px-3 py-2 text-xs text-secondary hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
            >准备四账户并排对照</button>
            <span className="text-[10px] text-muted">只建账户，不启用自动模拟、不创建订单。</span>
          </div>
        </>
      )}

      {progress && (
        <div className="mt-3 rounded-btn border border-border bg-base/40 p-3" aria-live="polite">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs font-medium text-foreground">{progress.label}</div>
            <div className="text-[11px] text-secondary">成功 {progress.completed.length} / {progress.total}{progress.active ? ` · 正在准备 ${progress.active.strategyName} ${fmtMoney(progress.active.initial_cash)}` : busy ? ' · 正在处理' : ''}</div>
          </div>
          {progress.completed.length > 0 && (
            <ul className="mt-2 space-y-1 text-[11px] text-success">
              {progress.completed.map((item, index) => <li key={`${targetKey(item.target)}-${index}`}>已准备 {item.target.strategyName} · {fmtMoney(item.target.initial_cash)} · {item.accountId}</li>)}
            </ul>
          )}
          {progress.failed.length > 0 && (
            <div className="mt-2 space-y-2">
              {progress.failed.map(item => (
                <div key={targetKey(item.target)} className="text-[11px] leading-5 text-danger" role="alert">
                  {item.target.strategyName} · {fmtMoney(item.target.initial_cash)}：{item.message}
                </div>
              ))}
              <button type="button" onClick={retryFailed} disabled={busy} className="rounded-btn border border-danger/30 px-2.5 py-1 text-[11px] text-danger disabled:opacity-40">
                重试失败项（{progress.failed.length}）
              </button>
            </div>
          )}
          {!busy && progress.failed.length === 0 && progress.completed.length === progress.total && (
            <p className="mt-2 text-[11px] text-muted">账户已就绪；新账户默认暂停，已有账户会保留当前启用状态。</p>
          )}
        </div>
      )}
    </section>
  )
}
