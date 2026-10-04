import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Clock3, Database, Loader2, Sparkles } from 'lucide-react'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { api } from '@/lib/api'
import {
  cryptoApi,
  type CryptoStrategyDraft,
  type CryptoStrategyDraftRequest,
  type CryptoStrategyDraftResult,
  type CryptoStrategyInterval,
  type CryptoSymbol,
} from './client'

const RESULT_STORAGE_KEY = 'stockfund.crypto-paper.ai-strategy-draft.v1'
const SYMBOLS: CryptoSymbol[] = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
const INITIAL_INPUT: DraftForm = {
  symbol: 'ETHUSDT',
  interval: '1h',
  leverage: '10',
  allocation_pct: '10',
  focus: '',
}

type DraftForm = Pick<CryptoStrategyDraftRequest, 'symbol' | 'interval'> & {
  leverage: string
  allocation_pct: string
  focus: string
}

interface SavedDraft {
  request: CryptoStrategyDraftRequest
  result: CryptoStrategyDraftResult
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isDraftRequest(value: unknown): value is CryptoStrategyDraftRequest {
  if (!isRecord(value)) return false
  const exchangeValid = value.exchange === 'binance' || value.exchange === 'bitget'
  const marketValid = value.market === 'spot' || value.market === 'usdm'
  return exchangeValid && marketValid &&
    !(value.market === 'spot' && value.exchange !== 'binance') &&
    (value.symbol === 'BTCUSDT' || value.symbol === 'ETHUSDT' || value.symbol === 'SOLUSDT') &&
    (value.interval === '1h' || value.interval === '4h') &&
    typeof value.leverage === 'number' && Number.isInteger(value.leverage) &&
    value.leverage >= 1 && value.leverage <= 20 &&
    (value.market !== 'spot' || value.leverage === 1) &&
    typeof value.allocation_pct === 'number' && Number.isFinite(value.allocation_pct) &&
    value.allocation_pct > 0 && value.allocation_pct <= 100 &&
    (value.focus === undefined || (typeof value.focus === 'string' && value.focus.length <= 600))
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isDraftResult(value: unknown): value is CryptoStrategyDraftResult {
  if (!isRecord(value) || !isRecord(value.draft) || !isRecord(value.evidence) || !isRecord(value.diagnostics)) return false
  const draft = value.draft
  const params = draft.strategy_params
  const validParams = isRecord(params) && (draft.strategy_id === 'ema_trend'
    ? isFiniteNumber(params.fast_period) && isFiniteNumber(params.slow_period)
    : draft.strategy_id === 'channel_breakout' && isFiniteNumber(params.lookback))
  const evidence = value.evidence
  const diagnostics = value.diagnostics
  const signalCountsValid = isRecord(diagnostics.signal_counts) &&
    Object.values(diagnostics.signal_counts).every(count => isFiniteNumber(count) && count >= 0)
  return typeof draft.name === 'string' && draft.name.length > 0 &&
    (draft.exchange === 'binance' || draft.exchange === 'bitget') &&
    (draft.market === 'spot' || draft.market === 'usdm') &&
    !(draft.market === 'spot' && draft.exchange !== 'binance') &&
    (draft.symbol === 'BTCUSDT' || draft.symbol === 'ETHUSDT' || draft.symbol === 'SOLUSDT') &&
    (draft.interval === '1h' || draft.interval === '4h') &&
    (draft.strategy_id === 'ema_trend' || draft.strategy_id === 'channel_breakout') &&
    validParams &&
    isFiniteNumber(draft.leverage) && draft.leverage >= 1 && draft.leverage <= 20 &&
    isFiniteNumber(draft.initial_cash) && draft.initial_cash > 0 &&
    isFiniteNumber(draft.allocation_pct) && draft.allocation_pct > 0 && draft.allocation_pct <= 100 &&
    isFiniteNumber(draft.stop_loss_pct) && draft.stop_loss_pct > 0 && draft.stop_loss_pct <= 100 &&
    isFiniteNumber(draft.take_profit_pct) && draft.take_profit_pct > 0 && draft.take_profit_pct <= 100 &&
    (draft.market !== 'spot' || draft.leverage === 1) &&
    evidence.exchange === draft.exchange && evidence.market === draft.market &&
    evidence.symbol === draft.symbol && evidence.interval === draft.interval &&
    ['retrieved_at_ms', 'quote_asof_ms', 'data_start_ms', 'data_end_ms', 'bars_count', 'return_pct', 'realized_volatility_pct', 'atr_pct', 'max_close_drawdown_pct', 'spread_bps'].every(key => isFiniteNumber(evidence[key])) &&
    isFiniteNumber(evidence.bars_count) && evidence.bars_count >= 0 &&
    typeof evidence.taker_fee_rate === 'string' &&
    (evidence.funding_rate === null || typeof evidence.funding_rate === 'string') &&
    (evidence.stats_method === undefined || typeof evidence.stats_method === 'string') &&
    typeof diagnostics.latest_signal === 'string' && signalCountsValid &&
    isFiniteNumber(diagnostics.evaluated_bars) && diagnostics.evaluated_bars >= 0 &&
    (diagnostics.method === undefined || typeof diagnostics.method === 'string') &&
    typeof value.rationale === 'string' &&
    Array.isArray(value.risk_notes) && value.risk_notes.every(note => typeof note === 'string') &&
    typeof value.model === 'string' && typeof value.provider === 'string' &&
    isFiniteNumber(value.created_at_ms)
}

function loadSavedDraft(): SavedDraft | null {
  try {
    const raw = window.sessionStorage.getItem(RESULT_STORAGE_KEY)
    if (!raw) return null
    const value: unknown = JSON.parse(raw)
    if (!isRecord(value) || !isDraftRequest(value.request) || !isDraftResult(value.result) ||
      value.result.draft.exchange !== value.request.exchange ||
      value.result.draft.market !== value.request.market ||
      value.result.draft.symbol !== value.request.symbol ||
      value.result.draft.interval !== value.request.interval) return null
    return { request: value.request, result: value.result }
  } catch {
    return null
  }
}

function inputFromRequest(request: CryptoStrategyDraftRequest): DraftForm {
  return {
    symbol: request.symbol,
    interval: request.interval,
    leverage: String(request.leverage),
    allocation_pct: String(request.allocation_pct),
    focus: request.focus ?? '',
  }
}

function formatTime(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

function formatPercent(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatRate(value: string | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return `${(Number(value) * 100).toFixed(4)}%`
}

function strategyParamsLabel(draft: CryptoStrategyDraft) {
  return 'fast_period' in draft.strategy_params
    ? `EMA ${draft.strategy_params.fast_period} / ${draft.strategy_params.slow_period}`
    : `通道回看 ${draft.strategy_params.lookback} 根 K 线`
}

function requestError(cause: unknown) {
  return cause instanceof Error ? cause.message : 'AI 策略草案请求失败'
}

function shortFocus(value: string) {
  return value.length > 120 ? `${value.slice(0, 120)}…` : value
}

export function AiStrategyDraftPanel({ onApply }: { onApply: (draft: CryptoStrategyDraft) => void }) {
  const [saved] = useState(loadSavedDraft)
  const [input, setInput] = useState<DraftForm>(() => saved ? inputFromRequest(saved.request) : INITIAL_INPUT)
  const [result, setResult] = useState<CryptoStrategyDraftResult | null>(saved?.result ?? null)
  const [resultRequest, setResultRequest] = useState<CryptoStrategyDraftRequest | null>(saved?.request ?? null)
  const [isGenerating, setIsGenerating] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState(saved ? '已恢复本次会话保存的策略草案。' : '')
  const [savedToSession, setSavedToSession] = useState(Boolean(saved))
  const controllerRef = useRef<AbortController | null>(null)
  const modelStatus = useQuery({ queryKey: ['crypto-paper-strategy-ai-status'], queryFn: api.strategyAiStatus, staleTime: 60_000 })

  useEffect(() => () => {
    controllerRef.current?.abort()
    controllerRef.current = null
  }, [])

  useEffect(() => {
    if (!result || !resultRequest) return
    try {
      window.sessionStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify({ request: resultRequest, result }))
      setSavedToSession(true)
    } catch {
      // Session storage may be unavailable; the in-page draft remains usable.
      setSavedToSession(false)
    }
  }, [result, resultRequest])

  const generate = async () => {
    setError('')
    setNotice('')
    const leverage = Number(input.leverage)
    const allocation = Number(input.allocation_pct)
    const focus = input.focus.trim()
    if (!Number.isInteger(leverage) || leverage < 1 || leverage > 20) {
      setError('合约杠杆须为 1 至 20 的整数。')
      return
    }
    if (!Number.isFinite(allocation) || allocation <= 0 || allocation > 100) {
      setError('单次仓位须大于 0 且不超过 100%。')
      return
    }
    if (focus.length > 600) {
      setError('补充关注点不能超过 600 个字符。')
      return
    }

    const request: CryptoStrategyDraftRequest = {
      exchange: 'bitget',
      market: 'usdm',
      symbol: input.symbol,
      interval: input.interval,
      leverage,
      allocation_pct: allocation,
      ...(focus ? { focus } : {}),
    }
    const controller = new AbortController()
    controllerRef.current?.abort()
    controllerRef.current = controller
    setIsGenerating(true)
    try {
      const response: unknown = await cryptoApi.strategyDraft(request, controller.signal)
      if (controller.signal.aborted) return
      if (!isDraftResult(response) || response.draft.exchange !== request.exchange ||
        response.draft.market !== request.market || response.draft.symbol !== request.symbol ||
        response.draft.interval !== request.interval) throw new Error('AI 策略草案响应与请求条件不一致')
      const next = response
      setResult(next)
      setResultRequest(request)
      setNotice('草案生成完成。请先核对策略和证据，再决定是否填入创建表。')
    } catch (cause) {
      if (controller.signal.aborted) return
      setError(requestError(cause))
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null
        setIsGenerating(false)
      }
    }
  }

  const cancel = () => {
    controllerRef.current?.abort()
    controllerRef.current = null
    setIsGenerating(false)
    setNotice('已停止等待。模型请求可能仍在完成，立即重试可能提示已有草案正在生成。')
  }

  const configured = modelStatus.data?.configured
  const draft = result?.draft
  const readOnlyDraft = Boolean(draft && (draft.exchange !== 'bitget' || draft.market !== 'usdm'))
  const evidence = result?.evidence
  const diagnostics = result?.diagnostics
  const riskNotes = result?.risk_notes ?? []

  return <section className="space-y-3 rounded-btn border border-accent/30 bg-accent/[0.035] p-3">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="flex items-center gap-2 text-sm font-semibold"><Sparkles className="h-4 w-4 text-accent" />AI 加密策略草案</div>
        <p className="mt-1 text-xs leading-relaxed text-muted">仅为 Bitget USDT 本位合约读取公开 K 线、计算样本和信号证据，再请求已配置的模型生成参数建议。草案只会填入下方表单，不会自动创建或启用账户。</p>
      </div>
      <div className="text-right text-[11px] text-muted">
        {modelStatus.isLoading ? '正在检查 AI 配置…' : modelStatus.isError ? 'AI 配置状态暂不可用；生成请求仍会由服务端校验。' : configured === false
          ? <span className="text-warning">尚未配置 AI 模型 · <Link className="text-accent underline" to="/settings?tab=ai">前往 AI 设置</Link></span>
          : configured ? `AI 模型已配置${modelStatus.data?.provider ? ` · ${modelStatus.data.provider}` : ''}` : 'AI 配置状态未知'}
      </div>
    </div>

    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <div className="text-xs text-muted">交易所<div className="mt-1 rounded-btn border border-border bg-base p-2 text-foreground">Bitget</div></div>
      <div className="text-xs text-muted">市场<div className="mt-1 rounded-btn border border-border bg-base p-2 text-foreground">USDT 本位合约</div></div>
      <label className="text-xs text-muted">交易对<select aria-label="AI 草案交易对" value={input.symbol} onChange={event => setInput(current => ({ ...current, symbol: event.target.value as CryptoSymbol }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground">{SYMBOLS.map(symbol => <option key={symbol}>{symbol}</option>)}</select></label>
      <label className="text-xs text-muted">K 线周期<select aria-label="AI 草案周期" value={input.interval} onChange={event => setInput(current => ({ ...current, interval: event.target.value as CryptoStrategyInterval }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground"><option value="1h">1 小时</option><option value="4h">4 小时</option></select></label>
      <label className="text-xs text-muted">杠杆<input aria-label="AI 草案杠杆" type="number" min="1" max="20" step="1" value={input.leverage} onChange={event => setInput(current => ({ ...current, leverage: event.target.value }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
      <label className="text-xs text-muted">单次仓位（%）<input aria-label="AI 草案单次仓位" type="number" min="0.000001" max="100" step="any" value={input.allocation_pct} onChange={event => setInput(current => ({ ...current, allocation_pct: event.target.value }))} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
      <label className="text-xs text-muted sm:col-span-2">补充关注点（可选）<textarea aria-label="AI 草案关注点" maxLength={600} rows={2} value={input.focus} onChange={event => setInput(current => ({ ...current, focus: event.target.value }))} placeholder="例如：关注趋势过滤与较低换手；最多 600 字" className="mt-1 block w-full resize-y rounded-btn border border-border bg-base p-2 text-foreground placeholder:text-muted" /></label>
    </div>

    {isGenerating && <div role="status" aria-live="polite" className="flex flex-wrap items-center justify-between gap-2 rounded-btn border border-border bg-surface px-3 py-2 text-xs text-secondary">
      <span className="inline-flex items-center gap-2"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在获取指定交易所的行情证据并生成策略草案；模型调用可能需要较长时间。</span>
      <button type="button" onClick={cancel} className="rounded-btn border border-border px-2.5 py-1 text-secondary hover:text-foreground">停止等待</button>
    </div>}
    {error && <div role="alert" className="rounded-btn border border-danger/40 bg-danger/10 p-3 text-xs text-danger">{error}</div>}
    {notice && <div role="status" className="text-xs text-secondary">{notice}</div>}
    {result && <div className="text-[11px] text-muted">{savedToSession ? '草案与请求条件已保存在当前浏览器会话中。' : '浏览器会话存储不可用；草案目前只保留在本页。'}</div>}

    <div className="flex flex-wrap items-center gap-3">
      <button type="button" onClick={() => { void generate() }} disabled={isGenerating || configured === false} className="inline-flex items-center gap-2 rounded-btn bg-accent px-3 py-2 text-xs font-medium text-white disabled:opacity-50"><Sparkles className="h-3.5 w-3.5" />{isGenerating ? '生成中…' : result ? '重新生成草案' : '生成策略草案'}</button>
      {configured === false && <span className="text-[11px] text-warning">请先配置 AI 模型。</span>}
    </div>

    {draft && evidence && diagnostics && <div className="space-y-3 rounded-btn border border-border bg-surface p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-foreground">{draft.name}</div>
          <div className="mt-1 text-xs text-secondary">{draft.exchange === 'bitget' ? 'Bitget' : 'Binance'} · {draft.market === 'spot' ? '现货' : 'U 本位合约'} · {draft.symbol} · {draft.interval} · {draft.strategy_id === 'ema_trend' ? 'EMA 趋势' : '通道突破'}</div>
          <div className="mt-1 text-xs text-muted">{strategyParamsLabel(draft)} · 杠杆 {draft.leverage}x · 仓位 {draft.allocation_pct}% · 初始资金 {draft.initial_cash.toLocaleString()} USDT · 止损 / 止盈 {draft.stop_loss_pct}% / {draft.take_profit_pct}%</div>
        </div>
        <button type="button" onClick={() => { if (!readOnlyDraft) { onApply(draft); setNotice('已将草案填入下方创建表。请核对策略参数后，再手动创建暂停账户。') } }} disabled={readOnlyDraft} title={readOnlyDraft ? 'Binance 历史草案仅供查看，不能用于创建 Bitget 账户。' : undefined} className="shrink-0 rounded-btn border border-accent/50 px-3 py-2 text-xs font-medium text-accent hover:bg-accent/10 disabled:cursor-not-allowed disabled:opacity-50">填入账户创建表</button>
      </div>
      {readOnlyDraft && <p className="rounded-btn border border-border bg-elevated/30 p-2 text-[11px] leading-relaxed text-muted">这是恢复的 Binance 历史草案，仅供查看。新生成的草案和新建账户固定使用 Bitget USDT 本位合约。</p>}

      <div className="rounded-btn border border-amber-500/30 bg-amber-500/5 p-3 text-xs leading-relaxed text-amber-300">下方指标是所示 K 线样本的行情统计与规则信号检查；没有执行逐笔回测，不是策略表现或未来收益预测。</div>
      <p className="text-[11px] leading-relaxed text-secondary">固定执行口径：信号后按实际取得的公开报价模拟成交，加入 5 bps 滑点；止盈止损按轮询观察到的价格检查，不按历史 K 线高低价还原盘中成交。AI 说明不会改变这些规则。</p>

      <div className="grid gap-2 text-[11px] sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-btn border border-border p-2 text-muted">样本区间 <b className="mt-1 block text-foreground">{formatTime(evidence.data_start_ms)} — {formatTime(evidence.data_end_ms)}</b></div>
        <div className="rounded-btn border border-border p-2 text-muted">K 线数量 <b className="mt-1 block text-foreground">{evidence.bars_count}</b></div>
        <div className="rounded-btn border border-border p-2 text-muted">样本行情涨跌幅（非回测）<b className="mt-1 block text-foreground">{formatPercent(evidence.return_pct)}</b></div>
        <div className="rounded-btn border border-border p-2 text-muted">单根 K 线波动（未年化）<b className="mt-1 block text-foreground">{formatPercent(evidence.realized_volatility_pct)}</b>{evidence.stats_method && <span className="mt-1 block text-[10px]">方法：{evidence.stats_method}</span>}</div>
        <div className="rounded-btn border border-border p-2 text-muted">ATR / 收盘最大回撤 <b className="mt-1 block text-foreground">{formatPercent(evidence.atr_pct)} / {formatPercent(evidence.max_close_drawdown_pct)}</b></div>
        <div className="rounded-btn border border-border p-2 text-muted">公开 Taker 费率 / 点差 <b className="mt-1 block text-foreground">{formatRate(evidence.taker_fee_rate)} / {evidence.spread_bps.toFixed(2)} bps</b></div>
        <div className="rounded-btn border border-border p-2 text-muted">资金费率 <b className="mt-1 block text-foreground">{formatRate(evidence.funding_rate)}</b></div>
        <div className="rounded-btn border border-border p-2 text-muted">报价时间 <b className="mt-1 block text-foreground">{formatTime(evidence.quote_asof_ms)}</b></div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="min-w-0 rounded-btn border border-border p-3">
          <div className="mb-2 text-xs font-semibold">草案理由</div>
          <MarkdownRenderer content={result?.rationale || '模型未返回说明。'} />
        </div>
        <div className="space-y-3 rounded-btn border border-border p-3">
          <div>
            <div className="text-xs font-semibold">策略规则信号检查（非回测）</div>
            <div className="mt-1 text-[11px] text-secondary">检查 K 线 {diagnostics.evaluated_bars} 根 · 最近信号 <code>{diagnostics.latest_signal || '—'}</code></div>
            <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-muted">{Object.entries(diagnostics.signal_counts).map(([signal, count]) => <span key={signal} className="rounded-full border border-border px-2 py-0.5">{signal}: {count}</span>)}</div>
            {diagnostics.method && <p className="mt-1 text-[10px] text-muted">方法：{diagnostics.method}</p>}
          </div>
          <div>
            <div className="text-xs font-semibold">风险提示</div>
            {riskNotes.length ? <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-secondary">{riskNotes.map((note, index) => <li key={`${index}-${note}`}>{note}</li>)}</ul> : <p className="mt-1 text-[11px] text-muted">服务未返回额外风险提示。</p>}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2 text-[10px] text-muted">
        <span className="inline-flex items-center gap-1"><Database className="h-3 w-3 shrink-0" />数据条件：{resultRequest?.exchange} · {resultRequest?.market} · {resultRequest?.symbol} · {resultRequest?.interval} · {resultRequest?.leverage}x · 仓位 {resultRequest?.allocation_pct}%{resultRequest?.focus ? ` · 关注点：${shortFocus(resultRequest.focus)}` : ''}</span>
        <span className="inline-flex items-center gap-1"><Clock3 className="h-3 w-3" />行情读取 {formatTime(evidence.retrieved_at_ms)} · 草案生成 {formatTime(result?.created_at_ms)} · {result?.provider} / {result?.model}</span>
      </div>
    </div>}
  </section>
}
