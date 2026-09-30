import { useEffect, useRef, useState } from 'react'
import { Coins, RefreshCcw } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { cryptoApi, type CryptoAccount, type CryptoAction, type CryptoMarket, type CryptoQuote, type CryptoSymbol, type CryptoValuation } from './client'

const SYMBOLS: CryptoSymbol[] = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
const ACTIONS: Record<CryptoMarket, { value: CryptoAction; label: string }[]> = {
  spot: [{ value: 'buy', label: '买入' }, { value: 'sell', label: '卖出' }],
  usdm: [
    { value: 'open_long', label: '开多' }, { value: 'open_short', label: '开空' },
    { value: 'close_long', label: '平多' }, { value: 'close_short', label: '平空' },
  ],
}

function money(value: string | number | undefined, digits = 2) {
  const n = Number(value)
  return Number.isFinite(n) ? n.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '—'
}

export function CryptoPaperPage() {
  const [market, setMarket] = useState<CryptoMarket>('spot')
  const [symbol, setSymbol] = useState<CryptoSymbol>('BTCUSDT')
  const [action, setAction] = useState<CryptoAction>('buy')
  const [quantity, setQuantity] = useState('')
  const [leverage, setLeverage] = useState(1)
  const [account, setAccount] = useState<CryptoAccount | null>(null)
  const [valuation, setValuation] = useState<CryptoValuation | null>(null)
  const [quote, setQuote] = useState<CryptoQuote | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const pendingId = useRef<string | null>(null)

  const refresh = async () => {
    try {
      const [nextAccount, nextQuote, nextValuation] = await Promise.all([
        cryptoApi.account(), cryptoApi.quote(market, symbol), cryptoApi.valuation(),
      ])
      setAccount(nextAccount)
      setQuote(nextQuote)
      setValuation(nextValuation)
      setError('')
    } catch (cause) {
      setQuote(null)
      setValuation(null)
      setError(cause instanceof Error ? cause.message : '行情暂不可用')
    }
  }

  useEffect(() => {
    let alive = true
    setQuote(null)
    setValuation(null)
    Promise.all([cryptoApi.account(), cryptoApi.quote(market, symbol), cryptoApi.valuation()])
      .then(([nextAccount, nextQuote, nextValuation]) => {
        if (!alive) return
        setAccount(nextAccount)
        setQuote(nextQuote)
        setValuation(nextValuation)
        setError('')
      })
      .catch((cause) => { if (alive) { setValuation(null); setError(cause instanceof Error ? cause.message : '行情暂不可用') } })
    const timer = window.setInterval(() => {
      cryptoApi.quote(market, symbol)
        .then(nextQuote => { if (alive) { setQuote(nextQuote); setError('') } })
        .catch(cause => { if (alive) { setQuote(null); setError(cause instanceof Error ? cause.message : '行情暂不可用') } })
    }, 15_000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [market, symbol])

  const selectMarket = (next: CryptoMarket) => {
    setMarket(next)
    setAction(ACTIONS[next][0].value)
    pendingId.current = null
  }

  const place = async () => {
    if (!quote || busy || !Number.isFinite(Number(quantity)) || Number(quantity) <= 0) return
    setBusy(true)
    const requestId = pendingId.current || crypto.randomUUID()
    pendingId.current = requestId
    try {
      const result = await cryptoApi.order({ market, symbol, action, quantity, leverage, request_id: requestId })
      setAccount(result.account)
      setValuation(null)
      setQuantity('')
      setError('')
      pendingId.current = null
      void refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '模拟订单失败')
    } finally {
      setBusy(false)
    }
  }

  const wallet = account?.[market]
  const quotePrice = action === 'buy' || action === 'open_long' || action === 'close_short' ? quote?.ask : quote?.bid
  const spotPositions = account ? Object.entries(account.spot.positions) : []
  const futurePositions = account ? Object.entries(account.usdm.positions) : []

  return <div className="space-y-4 p-4 md:p-6">
    <PageHeader title="数字资产模拟" subtitle="币安公开行情 · 现货与 U 本位合约独立虚拟账本" titleExtra={<Coins className="h-4 w-4 text-accent" />} />
    <div className="rounded-card border border-amber-500/35 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-300">
      研究模拟，绝不连接真实账户。使用盘口最优价及固定示例手续费；未计盘口深度、资金费率、真实阶梯保证金与强平。
      合约结果不能视为币安实际成交或风险结果。行情中断时停止下单。
    </div>
    <div className="flex flex-wrap gap-2">
      {(['spot', 'usdm'] as const).map(item => <button key={item} onClick={() => selectMarket(item)}
        className={`rounded-btn border px-3 py-2 text-sm ${market === item ? 'border-accent bg-accent/15 text-accent' : 'border-border text-secondary'}`}>
        {item === 'spot' ? '现货模拟' : 'U 本位合约模拟'}
      </button>)}
      <button onClick={refresh} className="ml-auto flex items-center gap-1 rounded-btn border border-border px-3 py-2 text-xs text-secondary"><RefreshCcw className="h-3.5 w-3.5" />刷新</button>
    </div>
    {error && <div className="rounded-card border border-danger/40 bg-danger/10 p-3 text-sm text-danger">{error}</div>}
    <div className="grid gap-3 lg:grid-cols-[1.1fr_0.9fr]">
      <section className="rounded-card border border-border bg-surface p-4">
        <h2 className="text-sm font-semibold">行情与模拟下单</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-muted">交易对<select value={symbol} onChange={event => { setSymbol(event.target.value as CryptoSymbol); pendingId.current = null }} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground">{SYMBOLS.map(item => <option key={item}>{item}</option>)}</select></label>
          <label className="text-xs text-muted">方向<select value={action} onChange={event => { setAction(event.target.value as CryptoAction); pendingId.current = null }} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground">{ACTIONS[market].map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2 text-xs text-muted">
          <div className="rounded-btn border border-border p-2">买一 {money(quote?.bid, 4)}</div>
          <div className="rounded-btn border border-border p-2">卖一 {money(quote?.ask, 4)}</div>
          <div className="rounded-btn border border-border p-2">标记 {market === 'usdm' ? money(quote?.mark ?? undefined, 4) : '—'}</div>
        </div>
        <p className="mt-2 text-[11px] text-muted">{quote ? `更新于 ${new Date(quote.asof_ms).toLocaleString()} · 最小数量 ${quote.min_qty} · 步长 ${quote.step_size}` : '等待公开行情'}</p>
        {market === 'usdm' && <p className="mt-1 text-[11px] text-muted">最新资金费率 {quote?.last_funding_rate ?? '—'}（仅展示，模拟账本暂不扣收）</p>}
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-muted">数量<input value={quantity} onChange={event => { setQuantity(event.target.value); pendingId.current = null }} inputMode="decimal" placeholder={quote?.min_qty ?? '0.001'} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 font-mono text-foreground" /></label>
          {market === 'usdm' && <label className="text-xs text-muted">杠杆（研究模拟 1–5 倍）<select value={leverage} onChange={event => { setLeverage(Number(event.target.value)); pendingId.current = null }} className="mt-1 block w-full rounded-btn border border-border bg-base p-2 text-foreground">{[1, 2, 3, 4, 5].map(item => <option key={item} value={item}>{item}x</option>)}</select></label>}
        </div>
        <p className="mt-2 text-xs text-muted">参考成交价 {money(quotePrice, 4)} USDT · 名义金额约 {quantity && quotePrice ? money(Number(quantity) * Number(quotePrice), 2) : '—'} USDT</p>
        <button disabled={!quote || busy || !quantity} onClick={place} className="mt-3 rounded-btn bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{busy ? '处理中…' : '提交虚拟订单'}</button>
      </section>
      <section className="rounded-card border border-border bg-surface p-4">
        <h2 className="text-sm font-semibold">{market === 'spot' ? '现货' : '合约'}虚拟账户</h2>
        <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
          <div className="rounded-btn border border-border p-3"><span className="text-xs text-muted">可用 USDT</span><div className="mt-1 font-mono">{money(wallet?.cash)}</div></div>
          <div className="rounded-btn border border-border p-3"><span className="text-xs text-muted">估算总权益</span><div className="mt-1 font-mono">{money(valuation?.[market].equity)}</div></div>
          <div className="rounded-btn border border-border p-3"><span className="text-xs text-muted">已实现盈亏</span><div className="mt-1 font-mono">{money(wallet?.realized_pnl)}</div></div>
          <div className="rounded-btn border border-border p-3"><span className="text-xs text-muted">估算总盈亏</span><div className="mt-1 font-mono">{money(valuation?.[market].total_pnl)}</div></div>
        </div>
        <div className="mt-4 text-xs font-semibold">当前持仓</div>
        <div className="mt-2 space-y-2 text-xs">{market === 'spot' ?
          (spotPositions.length ? spotPositions.map(([code, pos]) => <div key={code} className="rounded-btn border border-border p-2">{code} · {pos.qty} · 成本 {money(pos.cost)} USDT</div>) : <p className="text-muted">暂无现货持仓</p>) :
          (futurePositions.length ? futurePositions.map(([code, pos]) => <div key={code} className="rounded-btn border border-border p-2">{code} · {pos.side === 'long' ? '多' : '空'} {pos.qty} · 入场 {money(pos.entry, 4)} · 保证金 {money(pos.margin)} · {pos.leverage}x</div>) : <p className="text-muted">暂无合约持仓</p>)}
        </div>
      </section>
    </div>
    <section className="rounded-card border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold">虚拟成交记录</h2>
      <div className="mt-2 overflow-x-auto"><table className="w-full min-w-[640px] text-left text-xs"><thead className="border-b border-border text-muted"><tr><th className="py-2">时间</th><th>市场</th><th>交易对</th><th>方向</th><th>数量</th><th>价格</th><th>手续费</th><th>已实现盈亏</th></tr></thead><tbody>{account?.trades.slice(-30).reverse().map(item => <tr key={item.id} className="border-b border-border/50"><td className="py-2">{new Date(item.at).toLocaleString()}</td><td>{item.market}</td><td>{item.symbol}</td><td>{item.action}</td><td>{item.quantity}</td><td>{money(item.price, 4)}</td><td>{money(item.fee, 4)}</td><td>{money(item.realized_pnl)}</td></tr>)}</tbody></table></div>
      {!account?.trades.length && <p className="mt-3 text-xs text-muted">尚无虚拟成交</p>}
    </section>
  </div>
}
