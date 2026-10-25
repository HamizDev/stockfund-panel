import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, Briefcase, RefreshCw } from 'lucide-react'
import { ResearchAIButton } from '@/components/research/ResearchAIButton'
import { dashboardPanel, KpiCell, SectionTitle } from '@/components/dashboard/Primitives'
import { api, type PaperAccountSummary } from '@/lib/api'
import { QK, RESEARCH_QUERY_KEYS } from '@/lib/queryKeys'
import { storage } from '@/lib/storage'

type RecordValue = Record<string, unknown>

const AI_INSTRUCTION = '请逐只分析本模拟账户持仓及风险，按当前分析规则给结论、依据、相反证据与复核条件；模拟不等于实盘。'
const AI_PROMPT_LIMIT = 3500

function record(value: unknown): RecordValue | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as RecordValue
    : null
}

function records(value: unknown): RecordValue[] {
  return Array.isArray(value) ? value.map(record).filter((item): item is RecordValue => item !== null) : []
}

function text(value: unknown, fallback = '—'): string {
  return typeof value === 'string' && value.trim() ? value : fallback
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function decimal(value: unknown, digits = 4): string {
  const number = finite(value)
  return number === null ? '—' : number.toFixed(digits)
}

function percent(value: unknown): string {
  const number = finite(value)
  return number === null ? '—' : `${(number * 100).toFixed(2)}%`
}

function count(value: unknown): string {
  const number = finite(value)
  return number === null ? '—' : Math.trunc(number).toLocaleString('zh-Hans-CN')
}

function money(value: unknown, currency: string): string {
  const number = finite(value)
  return number === null
    ? '—'
    : `${number.toLocaleString('zh-Hans-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`
}

function signClass(value: unknown): string {
  const number = finite(value)
  return number === null || number === 0 ? 'text-muted' : number > 0 ? 'text-bull' : 'text-bear'
}

function clip(value: unknown, limit: number, fallback = ''): string {
  const normalized = typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : fallback
  return normalized.slice(0, limit)
}

function promptNumber(value: unknown): number | null {
  const number = finite(value)
  return number === null ? null : Number(number.toPrecision(5))
}

function gapSummary(value: unknown, currency: string): unknown[] {
  if (typeof value === 'string') return [currency, clip(value, 80)]
  const gap = record(value)
  if (!gap) return [currency, '数据缺口']
  return [
    currency,
    clip(gap.symbol, 24),
    clip(gap.code, 36),
    finite(gap.available),
    finite(gap.required),
  ]
}

function correlationPairs(groups: RecordValue[], limit: number): unknown[][] {
  const pairs: { currency: string; left: string; right: string; value: number }[] = []
  for (const group of groups) {
    const risk = record(group.risk)
    const matrix = record(risk?.correlations)
    const symbols = Array.isArray(matrix?.symbols) ? matrix.symbols.map(item => text(item, '')) : []
    const values = Array.isArray(matrix?.values) ? matrix.values : []
    for (let left = 0; left < symbols.length; left++) {
      const row = Array.isArray(values[left]) ? values[left] as unknown[] : []
      for (let right = left + 1; right < symbols.length; right++) {
        const value = finite(row[right])
        if (value !== null) pairs.push({
          currency: text(group.currency, ''), left: symbols[left], right: symbols[right], value,
        })
      }
    }
  }
  return pairs
    .sort((left, right) => Math.abs(right.value) - Math.abs(left.value))
    .slice(0, limit)
    .map(pair => [clip(pair.currency, 8), clip(pair.left, 24), clip(pair.right, 24), promptNumber(pair.value)])
}

function buildPrompt(data: RecordValue | undefined, account: PaperAccountSummary | undefined): string {
  const groups = records(data?.groups)
  const allPositions = groups.flatMap(group => {
    const currency = text(group.currency, '')
    const risk = record(group.risk)
    const contributions = record(risk?.risk_contributions)
    return records(group.positions).map(position => ({
      currency,
      symbol: text(position.symbol, ''),
      name: text(position.name, ''),
      industry: text(position.industry, ''),
      weight: promptNumber(position.weight),
      riskContribution: promptNumber(contributions?.[text(position.symbol, '')]),
    }))
  })
  const allGaps = groups.flatMap(group => records(group.gaps).map(gap => gapSummary(gap, text(group.currency, ''))))
  allGaps.push(...records(data?.gaps).map(gap => gapSummary(gap, '')))

  const makePrompt = (compact: boolean) => {
    const groupRows = groups.map(group => {
      const risk = record(group.risk)
      const coverage = record(group.coverage)
      const concentration = record(group.concentration)
      const row: unknown[] = [
        clip(group.currency, compact ? 6 : 10),
        compact ? finite(group.observations) : [
          clip(coverage?.first_common_date, 10),
          clip(coverage?.last_common_date, 10),
          finite(group.observations),
        ],
        promptNumber(risk?.included_weight ?? coverage?.included_weight),
        promptNumber(concentration?.top1),
        promptNumber(concentration?.top3),
        promptNumber(concentration?.hhi),
        promptNumber(risk?.annualized_volatility),
        promptNumber(risk?.max_drawdown),
      ]
      return row
    })
    const summary: RecordValue = {
      group_fields: compact
        ? ['currency', 'returns', 'included_weight', 'top1', 'top3', 'hhi', 'annualized_volatility', 'max_drawdown']
        : ['currency', 'sample_start', 'sample_end', 'returns', 'included_weight', 'top1', 'top3', 'hhi', 'annualized_volatility', 'max_drawdown'],
      groups: groupRows,
      holding_fields: compact
        ? ['currency', 'symbol', 'weight', 'risk_contribution']
        : ['currency', 'symbol', 'name', 'industry', 'weight', 'risk_contribution'],
      holdings: allPositions.map(position => compact
        ? [clip(position.currency, 6), clip(position.symbol, 18), position.weight, position.riskContribution]
        : [clip(position.currency, 10), clip(position.symbol, 28), clip(position.name, 40), clip(position.industry, 20), position.weight, position.riskContribution]),
      correlation_pairs: compact ? [] : correlationPairs(groups, 5),
      gap_count: allGaps.length,
      gaps: compact ? [] : allGaps.slice(0, 5),
      account: clip(data?.account_name, compact ? 24 : 64, text(account?.name, account?.id ?? '')),
      ...(!compact && typeof data?.note === 'string' ? { note: clip(data.note, 180) } : {}),
      ...(allGaps.length > (compact ? 0 : 5) ? { omitted_gaps: allGaps.length - (compact ? 0 : 5) } : {}),
      ...(compact ? { compacted_for_length: true } : {}),
    }
    return `${AI_INSTRUCTION}\n结构化持仓摘要：${JSON.stringify(summary)}`
  }

  const detailed = makePrompt(false)
  if (detailed.length <= AI_PROMPT_LIMIT) return detailed
  const compact = makePrompt(true)
  if (compact.length <= AI_PROMPT_LIMIT) return compact

  const minimal = {
    groups: groups.map(group => {
      const risk = record(group.risk)
      const concentration = record(group.concentration)
      return [
        clip(group.currency, 4), finite(group.observations),
        promptNumber(risk?.included_weight), promptNumber(concentration?.top1),
        promptNumber(concentration?.top3), promptNumber(concentration?.hhi),
        promptNumber(risk?.annualized_volatility), promptNumber(risk?.max_drawdown),
      ]
    }),
    holdings: allPositions.map(position => [
      clip(position.currency, 4), clip(position.symbol, 12), position.weight, position.riskContribution,
    ]),
    gaps: allGaps.length,
    account: clip(data?.account_name, 12, text(account?.name, account?.id ?? '')),
    compacted_for_length: true,
  }
  return `${AI_INSTRUCTION}\n结构化摘要：${JSON.stringify(minimal)}`
}

function gapLabel(value: unknown): string {
  if (typeof value === 'string') return value
  const gap = record(value)
  if (!gap) return '数据缺口'
  const symbol = typeof gap.symbol === 'string' ? `${gap.symbol}：` : ''
  if (gap.code === 'history_missing') return `${symbol}缺少历史价格`
  if (gap.code === 'no_history_before_cutoff') return `${symbol}分析截止日前无历史价格`
  if (gap.code === 'insufficient_common_observations') {
    return `共同历史样本不足（${count(gap.available)}/${count(gap.required)} 条收益）`
  }
  return `${symbol}${text(gap.message, text(gap.code, '数据缺口'))}`
}

function renderGaps(gaps: unknown, currency?: string) {
  const rows = Array.isArray(gaps) ? gaps : []
  if (!rows.length) return null
  return <div className="mt-2 rounded-md border border-border/70 bg-elevated/40 px-2 py-1.5 text-[10px] text-muted">
    <div className="font-medium text-secondary">{currency ? `${currency} 数据缺口` : '数据缺口'} · {rows.length}</div>
    <ul className="mt-1 space-y-0.5">{rows.slice(0, 6).map((gap, index) => <li key={index} className="break-words">{gapLabel(gap)}</li>)}</ul>
    {rows.length > 6 && <div className="mt-1">另有 {rows.length - 6} 项</div>}
  </div>
}

function CorrelationTable({ risk }: { risk: RecordValue }) {
  const matrix = record(risk.correlations)
  const symbols = Array.isArray(matrix?.symbols) ? matrix.symbols.map(value => text(value, '')) : []
  const values = Array.isArray(matrix?.values) ? matrix.values : []
  return <details className="mt-2 min-w-0 rounded-md border border-border/70 bg-surface/50">
    <summary className="cursor-pointer list-none px-2 py-1.5 text-[11px] text-secondary marker:hidden">
      相关性矩阵 <span className="ml-1 text-[10px] text-muted">{symbols.length ? `${symbols.length} 只标的` : '暂不可用'}</span>
    </summary>
    {symbols.length > 0 && <div className="max-w-full overflow-x-auto px-2 pb-2">
      <table className="min-w-max border-collapse text-[10px]">
        <thead><tr><th className="sticky left-0 bg-surface px-2 py-1 text-left text-muted">标的</th>
          {symbols.map(symbol => <th key={symbol} className="px-2 py-1 font-medium text-secondary">{symbol}</th>)}
        </tr></thead>
        <tbody>{symbols.map((symbol, rowIndex) => {
          const row = Array.isArray(values[rowIndex]) ? values[rowIndex] as unknown[] : []
          return <tr key={symbol} className="border-t border-border/50">
            <th className="sticky left-0 bg-surface px-2 py-1 text-left font-medium text-secondary">{symbol}</th>
            {symbols.map((column, columnIndex) => {
              const value = finite(row[columnIndex])
              return <td key={column} className={`px-2 py-1 text-right font-mono tabular-nums ${signClass(value)}`}>
                {decimal(value, 2)}
              </td>
            })}
          </tr>
        })}</tbody>
      </table>
    </div>}
  </details>
}

function PortfolioGroup({ group }: { group: RecordValue }) {
  const currency = text(group.currency, '—')
  const risk = record(group.risk) ?? {}
  const concentration = record(group.concentration) ?? {}
  const coverage = record(group.coverage) ?? {}
  const positions = records(group.positions)
  const contributions = record(risk.risk_contributions)
  const start = text(coverage.first_common_date, '')
  const end = text(coverage.last_common_date, '')
  const period = start || end ? `${start || '—'} 至 ${end || '—'}` : '共同样本期未标注'
  const observed = count(group.observations)
  const historyCount = count(coverage.history_count)
  const positionCount = count(coverage.position_count ?? positions.length)
  const includedWeight = risk.included_weight ?? coverage.included_weight
  const groupGaps = Array.isArray(group.gaps) ? group.gaps : []

  return <section className="min-w-0 rounded-lg border border-border/80 bg-surface/50 p-2">
    <SectionTitle icon={Activity} title={`${currency} 持仓与风险`} hint={money(group.total_value, currency)} />
    <div className="mb-2 grid min-w-0 grid-cols-2 gap-1 sm:grid-cols-3 lg:grid-cols-6">
      <KpiCell label="历史收益样本" value={observed} sub={period} />
      <KpiCell label="历史覆盖比例" value={percent(includedWeight)} sub={`有历史 ${historyCount}/${positionCount} 只`} tone="accent" />
      <KpiCell label="集中度 TOP1 / TOP3" value={<>{percent(concentration.top1)}<span className="px-1 text-muted">/</span>{percent(concentration.top3)}</>} sub="按当前持仓价值权重" />
      <KpiCell label="HHI" value={decimal(concentration.hhi)} sub="当前持仓权重平方和" />
      <KpiCell label="年化波动率" value={<span className={signClass(risk.annualized_volatility)}>{percent(risk.annualized_volatility)}</span>} sub="基于静态权重历史收益" />
      <KpiCell label="最大回撤" value={<span className={signClass(risk.max_drawdown)}>{percent(risk.max_drawdown)}</span>} sub="历史组合路径试算" />
    </div>

    <div className="min-w-0 overflow-hidden rounded-md border border-border/70">
      <div className="max-w-full overflow-x-auto">
        <table className="min-w-[620px] w-full border-collapse text-xs">
          <thead className="bg-elevated/60 text-[10px] text-muted">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium">持仓</th>
              <th className="px-2 py-1.5 text-left font-medium">行业</th>
              <th className="px-2 py-1.5 text-right font-medium">当前权重</th>
              <th className="px-2 py-1.5 text-right font-medium">风险贡献</th>
              <th className="px-2 py-1.5 text-right font-medium">价格日期</th>
            </tr>
          </thead>
          <tbody>{positions.map((position, index) => {
            const symbol = text(position.symbol, `持仓 ${index + 1}`)
            const name = text(position.name, symbol)
            const contribution = contributions?.[symbol]
            return <tr key={`${symbol}-${index}`} className="border-t border-border/60 text-secondary">
              <td className="max-w-[240px] px-2 py-1.5">
                <div className="break-words font-medium text-foreground">{name}</div>
                <div className="break-all font-mono text-[10px] text-muted">{symbol}</div>
              </td>
              <td className="px-2 py-1.5 text-muted">{text(position.industry, '未分类')}</td>
              <td className="px-2 py-1.5 text-right font-mono tabular-nums">{percent(position.weight)}</td>
              <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${signClass(contribution)}`}>{percent(contribution)}</td>
              <td className="px-2 py-1.5 text-right font-mono text-[10px] text-muted">{text(position.price_date)}</td>
            </tr>
          })}</tbody>
        </table>
      </div>
    </div>
    <CorrelationTable risk={risk} />
    {typeof risk.price_basis==='string' && <p className="mt-1.5 break-words text-[10px] text-muted">价格口径：{text(risk.price_basis)}</p>}
    {renderGaps(groupGaps, currency)}
  </section>
}

export function PortfolioPanel({ initialAccount }: { initialAccount?: string } = {}) {
  const [selectedId, setSelectedId] = useState(()=>{
    const saved=storage.pageView.researchToolsPortfolio.get('')
    return initialAccount || (typeof saved==='string' ? saved : '')
  })
  useEffect(()=>{storage.pageView.researchToolsPortfolio.set(selectedId)},[selectedId])
  const accountsQuery = useQuery({ queryKey: QK.paperAccounts, queryFn: api.paperAccounts, staleTime: 60_000 })
  const accounts = Array.isArray(accountsQuery.data?.accounts) ? accountsQuery.data.accounts : []
  const selectedAccount = accounts.find(account => account.id === selectedId) ?? accounts[0]
  const accountId = selectedAccount?.id ?? ''
  const portfolioQuery = useQuery({
    queryKey: RESEARCH_QUERY_KEYS.portfolio(accountId),
    queryFn: ({ signal }) => api.researchPortfolio(accountId, signal),
    enabled: Boolean(accountId),
    staleTime: 5 * 60_000,
    gcTime: 30 * 60_000,
  })
  const data = portfolioQuery.data as RecordValue | undefined
  const groups = records(data?.groups)
  const hasPositions = groups.some(group => records(group.positions).length > 0)
  const prompt = buildPrompt(data, selectedAccount)

  return <section className={`${dashboardPanel} min-w-0`}>
    <SectionTitle icon={Briefcase} title="模拟账户持仓体检" hint="只读分析 · 静态权重历史试算，不代表账户真实净值" />
    <div className="mb-2 flex min-w-0 flex-wrap items-end gap-2">
      <label className="flex min-w-[180px] flex-1 flex-col gap-1 text-[10px] text-muted sm:max-w-sm">
        模拟账户
        <select
          aria-label="选择模拟账户"
          className="min-h-9 min-w-0 rounded-md border border-border bg-elevated px-2 text-xs text-foreground outline-none focus:border-accent sm:min-h-8"
          value={accountId}
          disabled={accountsQuery.isLoading || accounts.length === 0}
          onChange={event => setSelectedId(event.target.value)}
        >
          {!accounts.length && <option value="">{accountsQuery.isLoading ? '读取模拟账户…' : '暂无模拟账户'}</option>}
          {accounts.map(account => <option key={account.id} value={account.id}>{account.name} · {account.id}</option>)}
        </select>
      </label>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <ResearchAIButton label="AI解读持仓" prompt={prompt} disabled={!hasPositions} />
        <button
          type="button"
          aria-label="刷新组合体检"
          title="刷新当前模拟账户体检"
          disabled={!accountId || portfolioQuery.isFetching}
          onClick={() => { void portfolioQuery.refetch() }}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-btn border border-border bg-elevated px-2.5 text-xs text-secondary hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-8"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${portfolioQuery.isFetching ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>
    </div>
    <p className="mb-2 break-words text-[10px] leading-relaxed text-muted">
      持仓权重按当前数量与价格计算；风险仅对有历史的标的按覆盖权重重归一，样本不足时显示为 —。不会自动调用模型或执行交易。
    </p>

    {accountsQuery.isLoading && <div className="py-4 text-center text-xs text-muted">正在读取模拟账户…</div>}
    {accountsQuery.isError && <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-bear/30 bg-bear/5 p-2 text-xs text-bear">
      <span>模拟账户读取失败。</span>
      <button type="button" onClick={() => { void accountsQuery.refetch() }} className="rounded border border-border px-2 py-1 text-secondary hover:text-foreground">重试</button>
    </div>}
    {!accountsQuery.isLoading && !accountsQuery.isError && accounts.length === 0 && <div className="rounded-md border border-dashed border-border p-4 text-center text-xs text-muted">还没有模拟账户。</div>}
    {accountId && portfolioQuery.isError && !data && <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-bear/30 bg-bear/5 p-2 text-xs text-bear">
      <span>读取持仓体检失败。</span>
      <button type="button" onClick={() => { void portfolioQuery.refetch() }} className="rounded border border-border px-2 py-1 text-secondary hover:text-foreground">重试读取</button>
    </div>}
    {accountId && portfolioQuery.isError && data && <div role="alert" className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-warning/30 bg-warning/5 px-2 py-1.5 text-[10px] text-warning">
      <span>刷新失败，仍保留上次可用结果。</span>
      <button type="button" onClick={() => { void portfolioQuery.refetch() }} className="rounded border border-border px-2 py-1 text-secondary hover:text-foreground">重试读取</button>
    </div>}
    {accountId && portfolioQuery.isLoading && <div className="py-4 text-center text-xs text-muted">正在读取 {selectedAccount?.name ?? '模拟账户'} 的持仓体检…</div>}
    {accountId && data && !hasPositions && <div className="rounded-md border border-dashed border-border p-4 text-center text-xs text-muted">当前模拟账户暂无持仓。</div>}
    {accountId && data && hasPositions && <div className="min-w-0 space-y-2">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[10px] text-muted">
        <span className="break-words">账户：{text(data.account_name, selectedAccount?.name ?? accountId)}</span>
        {portfolioQuery.isFetching && <span className="shrink-0">更新中，保留当前结果</span>}
      </div>
      {typeof data.note === 'string' && data.note.trim() && <p className="break-words rounded-md border border-border/70 bg-elevated/30 px-2 py-1.5 text-[10px] leading-relaxed text-secondary">{data.note}</p>}
      {groups.map((group, index) => <PortfolioGroup key={`${text(group.currency, 'currency')}-${index}`} group={group} />)}
      {renderGaps(data.gaps)}
    </div>}
  </section>
}
