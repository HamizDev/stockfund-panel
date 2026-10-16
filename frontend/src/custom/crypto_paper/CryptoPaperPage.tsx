import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Coins, RefreshCcw } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PageHeader } from '@/components/PageHeader'
import { QK } from '@/lib/queryKeys'
import { usePageState } from '@/lib/usePageState'
import { cryptoApi, type CryptoChartInterval, type CryptoStrategyAccount, type CryptoTicker, type CryptoSymbol } from './client'
import { CryptoMarketChart } from './CryptoMarketChart'
import { ResearchWorkflowPanel } from './ResearchWorkflowPanel'
import { StrategyAccountsPanel } from './StrategyAccountsPanel'
import { positionLevels, validQuote, validTicker } from './market-view'

const SYMBOLS: CryptoSymbol[] = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
const PERIODS: CryptoChartInterval[] = ['1m', '5m', '15m', '1h', '4h']
const PERIOD_NAMES = { '1m': '1 分钟', '5m': '5 分钟', '15m': '15 分钟', '1h': '1 小时', '4h': '4 小时' }
type CryptoMarketView = { symbol: CryptoSymbol; interval: CryptoChartInterval; selectedId: string | null }
const INITIAL_MARKET_VIEW: CryptoMarketView = { symbol: 'ETHUSDT', interval: '1h', selectedId: null }
function isCryptoMarketView(value: unknown): value is CryptoMarketView {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const view = value as Partial<CryptoMarketView>
  return SYMBOLS.includes(view.symbol as CryptoSymbol)
    && PERIODS.includes(view.interval as CryptoChartInterval)
    && (view.selectedId === null || (typeof view.selectedId === 'string' && view.selectedId.length > 0 && view.selectedId.length <= 200))
}
function isSelectableAccount(account: CryptoStrategyAccount | undefined): account is CryptoStrategyAccount {
  return !!account && account.exchange === 'bitget' && account.market === 'usdm' && account.effective_readonly !== true
}
function money(value: string | number | null | undefined, digits = 2) {
  if (value == null || value === '') return '—'
  const number = Number(value)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '—'
}

export function CryptoPaperPage() {
  const [marketView, setMarketView] = usePageState('cryptoMarket', INITIAL_MARKET_VIEW, isCryptoMarketView)
  const { symbol, interval, selectedId } = marketView
  const [selected, setSelected] = useState<CryptoStrategyAccount | null>(null)
  const [ticker, setTicker] = useState<CryptoTicker | null>(null)
  const [feedError, setFeedError] = useState<string | null>(null)
  const [now, setNow] = useState(Date.now())
  const quoteQuery = useQuery({ queryKey: QK.cryptoMarketQuote(symbol), queryFn: () => cryptoApi.marketQuote(symbol), refetchInterval: 15_000, retry: false })
  const candleQuery = useQuery({ queryKey: QK.cryptoCandles(symbol, interval), queryFn: () => cryptoApi.candles(symbol, interval), refetchInterval: 30_000, retry: false })
  const detailQuery = useQuery({ queryKey: QK.cryptoAccountDetail(selected?.id ?? ''), queryFn: () => cryptoApi.strategyAccountDetail(selected!.id), enabled: selected !== null, refetchInterval: 15_000, retry: false })
  useEffect(() => {
    setTicker(null)
    setFeedError(null)
    let alive = true
    const source = cryptoApi.marketStream(symbol)
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    source.onmessage = event => {
      if (!alive) return
      try {
        const payload = JSON.parse(event.data)
        if (payload.stream?.fresh && payload.stream?.connected && validTicker(payload.ticker, symbol, Date.now())) {
          setTicker(payload.ticker)
          setFeedError(null)
        } else {
          setTicker(null)
          setFeedError(typeof payload.stream?.error === 'string' ? payload.stream.error : '实时推送等待有效报价，当前尝试 REST')
        }
      } catch { setTicker(null); setFeedError('实时推送数据无效，当前尝试 REST') }
    }
    source.onerror = () => { if (alive) { setTicker(null); setFeedError('实时推送连接中断，当前尝试 REST') } }
    return () => { alive = false; source.close(); window.clearInterval(timer) }
  }, [symbol])
  const live = ticker && validTicker(ticker, symbol, now) ? ticker : null
  const rest = quoteQuery.data?.quote
  const restFresh = validQuote(rest, symbol, now)
  const price = live ?? (restFresh ? rest : null)
  const detail = !detailQuery.isError && detailQuery.data?.account.id === selected?.id ? detailQuery.data : null
  const levels = detail?.account.symbol === symbol ? positionLevels(detail.account) : []
  const refresh = () => { void quoteQuery.refetch(); void candleQuery.refetch(); if (selected) void detailQuery.refetch() }
  const handleAccountsLoaded = (accounts: CryptoStrategyAccount[]) => {
    const restored = selectedId ? accounts.find(account => account.id === selectedId) : undefined
    const account = isSelectableAccount(restored) ? restored : null
    if (selectedId && !account) {
      setMarketView(current => current.selectedId === selectedId ? { ...current, selectedId: null } : current)
    }
    setSelected(current => current?.id === account?.id ? current : account)
  }
  const selectAccount = (account: CryptoStrategyAccount) => {
    if (!isSelectableAccount(account)) return
    setSelected(account)
    setMarketView(current => ({ ...current, selectedId: account.id, symbol: account.symbol, interval: account.interval }))
  }
  return <div className="space-y-4 p-4 md:p-6">
    <PageHeader title="数字资产模拟" subtitle="Bitget 公开行情 · 独立策略账户与虚拟账本" titleExtra={<Coins className="h-4 w-4 text-accent" />} />
    <section className="rounded-card border border-accent/30 bg-accent/5 p-3 md:flex md:items-center md:gap-4">
      <div className="min-w-0 flex-1">
        <h2 className="text-sm font-semibold text-foreground">官方模拟交易 · 欧易 / Bitget</h2>
        <p className="mt-1 text-xs leading-relaxed text-muted">用下方入口连接模拟专用 Key；进入后单独启用模拟交易控制。本站本地模拟账户仍在本页「自动策略」区，使用独立虚拟账本。</p>
      </div>
      <div className="mt-3 flex flex-wrap gap-2 md:mt-0 md:shrink-0">
        <Link to="/crypto-demo?exchange=okx" className="rounded-btn border border-accent/40 bg-accent/10 px-3 py-2 text-xs font-medium text-accent transition-colors hover:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">欧易 OKX 官方模拟</Link>
        <Link to="/crypto-demo?exchange=bitget" className="rounded-btn border border-border bg-surface px-3 py-2 text-xs font-medium text-secondary transition-colors hover:border-accent hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">Bitget 官方模拟</Link>
      </div>
    </section>
    <div className="rounded-card border border-amber-500/35 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-300">当前只开放 Bitget USDT 本位合约模拟，无需交易 API Key。策略依据已收盘 K 线，后台每 30 秒检查一次；行情过期时暂停本轮模拟下单。新建账户默认暂停，10x/20x 为研究杠杆，费用与简化强平模型计入虚拟账本。</div>
    <section className="rounded-card border border-border bg-surface p-3 md:p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-sm font-semibold">Bitget 合约行情</h2>
        <label className="text-xs text-muted">交易对<select aria-label="图表交易对" value={symbol} onChange={event => { setSelected(null); setMarketView(current => ({ ...current, symbol: event.target.value as CryptoSymbol, selectedId: null })) }} className="ml-2 rounded-btn border border-border bg-base p-2 text-foreground">{SYMBOLS.map(item => <option key={item}>{item}</option>)}</select></label>
        <label className="text-xs text-muted">周期<select aria-label="图表周期" value={interval} onChange={event => setMarketView(current => ({ ...current, interval: event.target.value as CryptoChartInterval }))} className="ml-2 rounded-btn border border-border bg-base p-2 text-foreground">{PERIODS.map(item => <option key={item} value={item}>{PERIOD_NAMES[item]}</option>)}</select></label>
        <button onClick={refresh} className="flex items-center gap-1 rounded-btn border border-border px-3 py-2 text-xs text-secondary"><RefreshCcw className="h-3.5 w-3.5" />刷新</button>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <div className="rounded-btn border border-border p-2"><span className="text-muted">买一</span><div className="mt-1 font-mono">{money(price?.bid, 4)}</div></div>
        <div className="rounded-btn border border-border p-2"><span className="text-muted">卖一</span><div className="mt-1 font-mono">{money(price?.ask, 4)}</div></div>
        <div className="rounded-btn border border-border p-2"><span className="text-muted">标记价格</span><div className="mt-1 font-mono">{money(price?.mark, 4)}</div></div>
        <div className="rounded-btn border border-border p-2"><span className="text-muted">行情连接</span><div className={`mt-1 ${price ? 'text-accent' : 'text-amber-300'}`}>{live ? '公开实时推送' : restFresh ? 'REST 报价回退' : '行情暂不可用'}</div></div>
      </div>
      <p className="mt-2 text-[11px] text-muted">{price ? `行情时间 ${new Date(price.asof_ms).toLocaleString()} · ` : ''}仅展示已收盘 K 线，图表时间为北京时间。下方选择“在图表查看”可关联账户成交与风险线。</p>
      {feedError && !live && <p className="mt-2 text-xs text-amber-300">{feedError}</p>}
      {quoteQuery.error && !live && <p className="mt-2 text-xs text-danger">{quoteQuery.error.message}</p>}
      {candleQuery.error && <p className="mt-2 text-xs text-danger">{candleQuery.error.message}；已有历史图保留供查看。</p>}
      {candleQuery.isPending && <p className="mt-2 text-xs text-muted">正在获取 Bitget 历史 K 线…</p>}
      <CryptoMarketChart bars={candleQuery.data?.bars ?? []} symbol={symbol} interval={interval} market="usdm" trades={detail?.trades ?? []} levels={levels} />
      {selected && <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted"><span>图表关联：{selected.name} · {selected.symbol} · {selected.leverage}x</span><button onClick={() => { setSelected(null); setMarketView(current => ({ ...current, selectedId: null })) }} className="text-accent">清除关联</button>{detailQuery.error && <span className="text-danger">账户明细暂不可用，风险线待核对</span>}</div>}
      {levels.length > 0 && <p className="mt-2 text-[11px] text-muted">止盈止损线按入场价与账户百分比计算，未乘杠杆；成交仍受滑点、费用和轮询时点影响。</p>}
    </section>
    <StrategyAccountsPanel onInspectAccount={selectAccount} onAccountsLoaded={handleAccountsLoaded} />
    <ResearchWorkflowPanel />
  </div>
}
