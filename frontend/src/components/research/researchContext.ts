import type { PaperCompareRow, StrategyBacktestResult, StrategyDetail } from '@/lib/api'

const text = (value: unknown, limit = 240) => typeof value === 'string' ? value.slice(0, limit) : undefined
const finite = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : null
const select = (value: Record<string, unknown> | null | undefined, keys: string[]) =>
  Object.fromEntries(keys.filter(key => value?.[key] !== undefined).map(key => [key,
    typeof value![key] === 'string' ? text(value![key], 100) : typeof value![key] === 'boolean' ? value![key] : finite(value![key]),
  ]))

export function researchLink(page: 'screener' | 'backtest' | 'paper', strategyId?: string | null, assetType = 'stock') {
  const params = new URLSearchParams()
  if (strategyId) params.set('strategy', strategyId)
  params.set('asset_type', assetType === 'etf' ? 'etf' : 'stock')
  return `/${page}?${params}`
}

export function strategyResearchPrompt(strategy: StrategyDetail, assetType: string) {
  // Do not serialize a whole settings/result object into a model request.
  const summary = {
    id: text(strategy.id, 80), name: text(strategy.name, 100), description: text(strategy.description, 600),
    asset_type: assetType, timeframes: strategy.timeframes, execution_backend: strategy.execution_backend,
    entry_signals: strategy.entry_signals.slice(0, 12).map(signal => text(signal, 64)), exit_signals: strategy.exit_signals.slice(0, 12).map(signal => text(signal, 64)),
    stop_loss: finite(strategy.stop_loss), take_profit: finite(strategy.take_profit),
    max_hold_days: finite(strategy.max_hold_days),
  }
  return `请用新手能理解的中文解释这条已保存策略。先读取该策略的真实规则；区分描述与已验证的代码条件。按“选什么、何时买、何时卖、如何回测、哪些数据缺口会阻止交易”回答。说明日线信号与下一交易日开盘成交的区别，不自动运行回测或创建订单，不承诺盈利。\n策略摘要：${JSON.stringify(summary)}`
}

export function backtestResearchPrompt(result: StrategyBacktestResult) {
  const summary = {
    run_id: text(result.run_id, 100), strategy: select(result.strategy_info, ['id', 'name']),
    config: select(result.config, ['asset_type', 'start', 'end', 'mode', 'full_kind', 'initial_capital', 'matching', 'entry_fill', 'exit_fill', 'commission_pct', 'stamp_tax_pct', 'slippage_bps', 'max_positions', 'position_sizing']),
    stats: Object.fromEntries(['total_return', 'annual_return', 'max_drawdown', 'win_rate', 'n_trades', 'profit_factor', 'sharpe', 'n_candidates', 'mode', 'full_kind'].map(key => [key, typeof result.stats[key] === 'string' ? text(result.stats[key]) : finite(result.stats[key])])),
    observed_days: result.equity_curve.length,
    observed_start: result.equity_curve[0]?.date,
    observed_end: result.equity_curve.at(-1)?.date,
  }
  return `请解读下面这一次实际成功的历史回测（即使页面当前选了其他策略，也只讨论这个 run_id）。收益率、回撤和胜率是小数制；费用也是比例，滑点为 bps。不要从胜率或交易次数反推胜单数。说明样本数、费后收益、回撤、盈亏比、样本外验证和数据限制；区分候选前瞻统计与可执行账户回测。给出下一步虚拟仓观察清单，不替我下单、不自动优化、不保证未来成功率。\n回测摘要：${JSON.stringify(summary)}`
}

export function paperResearchPrompt(rows: PaperCompareRow[]) {
  const summary = rows.slice(0, 12).map(row => ({
    strategy_id: text(row.strategy_id, 80), market: row.asset_type, auto_enabled: row.auto_enabled,
    settled_pnl_pct: finite(row.settled_pnl_pct), rounds: row.rounds, wins: row.wins ?? null,
    win_rate_pct: row.rounds > 0 ? finite(row.win_rate) : null,
    max_drawdown_pct: finite(row.settled_max_drawdown), nav_quality: row.settled_nav_status ?? 'unavailable', holdings_count: row.holdings_count ?? null,
    nav_date: row.nav_date ?? null,
  }))
  return `请解释这些独立虚拟策略账户的实际模拟表现，用中文说明收益、回撤、已平仓胜单/回合及样本量。这里百分比字段是百分数值，不是小数制。零回合没有可统计的胜率。净值仅在质量已确认时提供；缺行情或旧记录质量未知时不得推断收益，日期缺失不能当作今天；不得凭汇总推断没成交的具体原因。可核对策略监控与数据能力；不修改设置、不运行策略或创建订单。\n按定版收益排列，账户总数${rows.length}，展示${summary.length}个，遗漏${Math.max(0, rows.length - summary.length)}个：${JSON.stringify(summary)}`
}
