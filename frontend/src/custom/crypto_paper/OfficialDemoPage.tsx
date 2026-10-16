import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Eye, RefreshCcw, ShieldCheck, Unplug } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { Modal } from '@/components/Modal'
import { PageHeader } from '@/components/PageHeader'
import { QK } from '@/lib/queryKeys'
import {
  cryptoApi,
  type CryptoChartInterval,
  type CryptoSymbol,
  type DemoAccountMode,
  type DemoAccountState,
  type DemoAccountsResult,
  type DemoConnectRequest,
  type DemoExchange,
  type DemoFill,
  type DemoOrder,
  type DemoPosition,
  type DemoSite,
  type DemoSnapshot,
} from './client'
import { CryptoMarketChart } from './CryptoMarketChart'
import { OfficialDemoTradingPanel } from './OfficialDemoTradingPanel'
import { OfficialDemoBotsPanel } from './OfficialDemoBotsPanel'

const SYMBOLS: CryptoSymbol[] = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
const INTERVALS: CryptoChartInterval[] = ['1m', '5m', '15m', '1h', '4h']
const INTERVAL_LABELS: Record<CryptoChartInterval, string> = {
  '1m': '1 分钟',
  '5m': '5 分钟',
  '15m': '15 分钟',
  '1h': '1 小时',
  '4h': '4 小时',
}
const EXCHANGES: DemoExchange[] = ['okx', 'bitget']
const OKX_DOCS = 'https://www.okx.com/docs-v5/en/'
const BITGET_CLASSIC_DOCS = 'https://www.bitget.com/docs/classic/demo-trading/rest-api'
const BITGET_UTA_DOCS = 'https://www.bitget.com/docs/uta/demo-trading/rest-api'
const INPUT_CLASS = 'mt-1 w-full min-w-0 rounded-btn border border-border bg-base px-3 py-2 text-sm text-foreground outline-none focus:border-accent focus:ring-1 focus:ring-accent disabled:opacity-60'
const BUTTON_CLASS = 'inline-flex min-h-9 items-center justify-center gap-2 rounded-btn border border-border px-3 py-2 text-xs font-medium text-secondary transition-colors hover:border-accent hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50'

type DemoTab = 'balances' | 'positions' | 'orders' | 'fills'
type Credentials = { apiKey: string; secretKey: string; passphrase: string }

const TABS: Array<{ id: DemoTab; label: string }> = [
  { id: 'balances', label: '余额' },
  { id: 'positions', label: '持仓' },
  { id: 'orders', label: '当前委托' },
  { id: 'fills', label: '最近成交' },
]

function isDemoExchange(value: string | null): value is DemoExchange {
  return value === 'okx' || value === 'bitget'
}

function exchangeName(exchange: DemoExchange) {
  return exchange === 'okx' ? 'OKX' : 'Bitget'
}

function defaultDemoAccountMode(exchange: DemoExchange): DemoAccountMode {
  return exchange === 'bitget' ? 'uta' : 'classic'
}

function safeConnectError(error: unknown, exchange: DemoExchange, accountMode: DemoAccountMode) {
  const context = `${exchangeName(exchange)}${exchange === 'bitget' ? ` ${accountMode.toUpperCase()}` : ''}`
  const message = error instanceof Error ? error.message : ''
  const match = /\[demo-error:(exchange_code|exchange_error|http_status|network|invalid_response)(?::([0-9]{1,8}))?\]$/.exec(message)
  const category = match?.[1]
  const code = match?.[2]
  const modeGuidance = exchange === 'bitget' ? '本页不会自动尝试另一账户模式。' : ''

  if (category === 'exchange_code' && code) {
    return `${context} 模拟连接未成功（交易所错误码 ${code}）。该代码不能单独确认具体原因；请核对当前模式的专用 Demo Key 和只读权限。${modeGuidance}输入已清空。`
  }
  if (category === 'exchange_error') {
    return `${context} 模拟连接未成功（交易所返回了非数字错误码）。原始错误内容已屏蔽；请核对当前模式的专用 Demo Key 和只读权限。${modeGuidance}输入已清空。`
  }
  if (category === 'http_status') {
    const safeStatus = code && /^[1-5][0-9]{2}$/.test(code) ? `HTTP ${code}` : 'HTTP 状态未知'
    return `${context} 模拟连接未成功（${safeStatus}）。该状态不能单独确认具体原因；请核对接口可达性和只读权限。${modeGuidance}输入已清空。`
  }
  if (category === 'network') {
    return `${context} 模拟连接未完成（连接异常）。请检查网络和本机时间；认证结果尚未确认。${modeGuidance}输入已清空。`
  }
  if (category === 'invalid_response') {
    return `${context} 模拟连接未完成（响应格式异常）。请核对当前模式对应的模拟接口；认证结果尚未确认。${modeGuidance}输入已清空。`
  }
  return `${context} 模拟连接未成功（未知错误类别）。请核对当前模式和模拟盘专用只读 Key。${modeGuidance}输入已清空。`
}

function stateLabel(state: DemoAccountState) {
  if (state === 'connected') return '已连接'
  if (state === 'saved') return '本机已加密保存'
  return '未连接'
}

function showValue(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function showTime(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value) || value <= 0) return '—'
  return new Date(value).toLocaleString('zh-CN')
}

function snapshotTime(value: string | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN')
}

function sideLabel(value: string) {
  if (value === 'buy' || value === 'long') return '买入 / 多'
  if (value === 'sell' || value === 'short') return '卖出 / 空'
  return value || '—'
}

function Table({ columns, rows, emptyText }: { columns: string[]; rows: Array<{ key: string; cells: ReactNode[] }>; emptyText: string }) {
  if (rows.length === 0) {
    return <div className="rounded-btn border border-dashed border-border px-4 py-8 text-center text-sm text-muted">{emptyText}</div>
  }
  return <div className="overflow-x-auto rounded-btn border border-border">
    <table className="min-w-[680px] w-full border-collapse text-left text-xs">
      <thead className="bg-elevated text-muted">
        <tr>{columns.map(column => <th key={column} className="whitespace-nowrap px-3 py-2 font-medium">{column}</th>)}</tr>
      </thead>
      <tbody className="divide-y divide-border">
        {rows.map(row => <tr key={row.key} className="text-secondary">{row.cells.map((cell, index) => <td key={`${row.key}-${index}`} className="whitespace-nowrap px-3 py-2">{cell}</td>)}</tr>)}
      </tbody>
    </table>
  </div>
}

function accountStatusFor(accounts: DemoAccountsResult | undefined, exchange: DemoExchange) {
  return accounts?.accounts.find(item => item.exchange === exchange)
}

function fillRows(fills: DemoFill[]) {
  return fills.map(fill => ({
    key: fill.fill_id,
    cells: [
      showTime(fill.ts_ms), fill.symbol, sideLabel(fill.side), `${showValue(fill.quantity)} ${fill.quantity_unit}`,
      showValue(fill.price), showValue(fill.fee), fill.fee_currency || '—', fill.order_id || '—',
    ],
  }))
}

function orderRows(orders: DemoOrder[]) {
  return orders.map(order => ({
    key: order.order_id,
    cells: [
      showTime(order.created_at_ms), order.symbol, sideLabel(order.side), order.order_type || '—',
      `${showValue(order.quantity)} ${order.quantity_unit}`, showValue(order.price), showValue(order.filled_quantity), order.status || '—',
    ],
  }))
}

function positionRows(positions: DemoPosition[]) {
  return positions.map((position, index) => ({
    key: `${position.symbol}-${position.side}-${index}`,
    cells: [
      position.symbol, sideLabel(position.side), `${showValue(position.quantity)} ${position.quantity_unit}`,
      showValue(position.entry_price), showValue(position.mark_price), showValue(position.unrealized_pnl),
      showValue(position.leverage), position.margin_mode || '—',
    ],
  }))
}

function balanceRows(snapshot: DemoSnapshot) {
  return (snapshot.balances ?? []).map((balance, index) => ({
    key: `${balance.currency}-${index}`,
    cells: [balance.currency, showValue(balance.equity), showValue(balance.available), showValue(balance.unrealized_pnl)],
  }))
}

function tabContent(tab: DemoTab, snapshot: DemoSnapshot | undefined, configured: boolean) {
  if (!snapshot) {
    return <div className="rounded-btn border border-dashed border-border px-4 py-8 text-center text-sm text-muted">
      {configured ? '正在加载账户快照…' : '连接官方模拟账户后可查看此处数据。'}
    </div>
  }

  if (tab === 'balances') {
    return <div className="space-y-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <div className="rounded-btn border border-border bg-base p-3">
          <div className="text-xs text-muted">账户权益</div>
          <div className="mt-1 break-all font-mono text-lg text-foreground">{showValue(snapshot.total_equity)} <span className="text-xs text-muted">{snapshot.equity_ccy || ''}</span></div>
        </div>
        <div className="rounded-btn border border-border bg-base p-3">
          <div className="text-xs text-muted">账户模式</div>
          <div className="mt-1 text-sm text-foreground">{snapshot.account_mode.toUpperCase()}</div>
        </div>
      </div>
      <Table columns={['币种', '权益', '可用', '未实现盈亏（该币种）']} rows={balanceRows(snapshot)} emptyText="交易所当前没有返回余额记录。" />
    </div>
  }
  if (tab === 'positions') {
    return <Table columns={['交易对', '方向', '数量', '入场价', '标记价', '未实现盈亏（原币）', '杠杆', '保证金模式']} rows={positionRows(snapshot.positions ?? [])} emptyText="当前没有持仓。" />
  }
  if (tab === 'orders') {
    return <div className="space-y-2"><p className="text-[11px] text-muted">仅展示最近 100 笔合约委托；更早的委托不在此列表范围内。</p><Table columns={['创建时间', '交易对', '方向', '类型', '数量', '委托价', '已成交', '状态']} rows={orderRows(snapshot.orders ?? [])} emptyText="当前没有委托。" /></div>
  }
  return <div className="space-y-2"><p className="text-[11px] text-muted">仅展示最近 20 笔成交；更早的成交不在此列表范围内。</p><Table columns={['成交时间', '交易对', '方向', '数量', '成交价', '手续费', '手续费币种', '委托编号']} rows={fillRows(snapshot.fills ?? [])} emptyText="暂无最近成交记录。" /></div>
}

function ConfirmDialog({ open, pending, onCancel, onConfirm }: {
  open: boolean
  pending: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  if (!open) return null
  return <Modal
    ariaLabel="确认断开官方模拟账户"
    onClose={pending ? () => undefined : onCancel}
    closeOnBackdrop={!pending}
    panelClassName="w-[92vw] max-w-md rounded-card border border-border bg-surface p-5 shadow-xl"
  >
    <h2 className="text-sm font-semibold text-foreground">断开本机连接？</h2>
    <p className="mt-2 text-xs leading-relaxed text-muted">这会清除本站保存的连接配置并停止读取账户数据。不会撤销交易所 API Key，也不会修改官方模拟账户的持仓或委托。</p>
    <div className="mt-5 flex flex-wrap justify-end gap-2">
      <button type="button" onClick={onCancel} disabled={pending} className={BUTTON_CLASS}>取消</button>
      <button type="button" onClick={onConfirm} disabled={pending} className="inline-flex min-h-9 items-center justify-center gap-2 rounded-btn bg-danger px-3 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">
        {pending ? '正在断开…' : '确认断开'}
      </button>
    </div>
  </Modal>
}

export function OfficialDemoPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const urlExchange = searchParams.get('exchange')
  const exchange: DemoExchange = isDemoExchange(urlExchange) ? urlExchange : 'okx'
  const queryClient = useQueryClient()
  const actionContext = useRef({ exchange, generation: 0 })
  const [site, setSite] = useState<DemoSite>('global')
  const [accountMode, setAccountMode] = useState<DemoAccountMode>(() => defaultDemoAccountMode(exchange))
  const [credentials, setCredentials] = useState<Credentials>({ apiKey: '', secretKey: '', passphrase: '' })
  const [saveCredentials, setSaveCredentials] = useState(false)
  const [connectPending, setConnectPending] = useState(false)
  const [disconnectPending, setDisconnectPending] = useState(false)
  const [disconnectOpen, setDisconnectOpen] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)
  const [accountActionError, setAccountActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [symbol, setSymbol] = useState<CryptoSymbol>('BTCUSDT')
  const [interval, setInterval] = useState<CryptoChartInterval>('1h')
  const [activeTab, setActiveTab] = useState<DemoTab>('balances')
  const actionPending = connectPending || disconnectPending

  useEffect(() => {
    if (isDemoExchange(urlExchange)) return
    setSearchParams(current => {
      const next = new URLSearchParams(current)
      next.set('exchange', 'okx')
      return next
    }, { replace: true })
  }, [setSearchParams, urlExchange])

  useLayoutEffect(() => {
    if (actionContext.current.exchange === exchange) return
    actionContext.current = { exchange, generation: actionContext.current.generation + 1 }
    setCredentials({ apiKey: '', secretKey: '', passphrase: '' })
    setSaveCredentials(false)
    setSite('global')
    setAccountMode(defaultDemoAccountMode(exchange))
    setConnectError(null)
    setAccountActionError(null)
    setNotice(null)
    setDisconnectOpen(false)
    setActiveTab('balances')
  }, [exchange])

  const accountsQuery = useQuery({
    queryKey: QK.demoAccounts,
    queryFn: () => cryptoApi.demoAccounts(),
    retry: false,
    staleTime: 30_000,
  })
  const accountStatus = accountStatusFor(accountsQuery.data, exchange)
  const configured = accountsQuery.isSuccess && accountStatus?.configured === true
  const activeSite = configured ? accountStatus.site : site
  const activeAccountMode = configured ? accountStatus?.account_mode ?? accountMode : accountMode
  const persistenceSupported = accountsQuery.data?.persistence_supported === true

  useEffect(() => {
    if (!accountsQuery.isSuccess || configured) return
    const queryKey = QK.demoSnapshots(exchange)
    void queryClient.cancelQueries({ queryKey }).then(() => queryClient.removeQueries({ queryKey }))
  }, [accountsQuery.isSuccess, configured, exchange, queryClient])

  const quoteQuery = useQuery({
    queryKey: QK.demoMarketQuote(exchange, activeSite, symbol),
    queryFn: ({ signal }) => cryptoApi.demoMarketQuote(exchange, activeSite, symbol, signal),
    refetchInterval: 15_000,
    staleTime: 5_000,
    retry: false,
  })
  const candlesQuery = useQuery({
    queryKey: QK.demoCandles(exchange, activeSite, symbol, interval),
    queryFn: ({ signal }) => cryptoApi.demoCandles(exchange, activeSite, symbol, interval, signal),
    refetchInterval: 30_000,
    staleTime: 10_000,
    retry: false,
  })
  const snapshotQuery = useQuery({
    queryKey: QK.demoSnapshot(exchange, activeSite),
    queryFn: ({ signal }) => cryptoApi.demoAccount(exchange, signal),
    enabled: accountsQuery.isSuccess && configured && !disconnectPending,
    refetchInterval: configured && !disconnectPending ? 30_000 : false,
    retry: false,
  })
  const snapshot = configured ? snapshotQuery.data : undefined

  useEffect(() => {
    if (!configured || (!snapshotQuery.isError && !snapshot?.error)) return
    void queryClient.invalidateQueries({ queryKey: QK.demoAccounts })
  }, [configured, queryClient, snapshot?.error, snapshotQuery.isError])

  const selectExchange = (nextExchange: DemoExchange) => {
    if (actionPending) return
    setCredentials({ apiKey: '', secretKey: '', passphrase: '' })
    setSaveCredentials(false)
    setConnectError(null)
    setAccountActionError(null)
    setNotice(null)
    setSearchParams(current => {
      const next = new URLSearchParams(current)
      next.set('exchange', nextExchange)
      return next
    })
  }

  const handleConnect = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (actionPending) return
    const actionGeneration = actionContext.current.generation
    const isCurrentAction = () => actionContext.current.exchange === exchange && actionContext.current.generation === actionGeneration
    const body: DemoConnectRequest = {
      api_key: credentials.apiKey.trim(),
      secret_key: credentials.secretKey.trim(),
      passphrase: credentials.passphrase.trim(),
      site,
      account_mode: accountMode,
      save: saveCredentials && persistenceSupported,
    }
    // Keep credentials out of React Query mutation state and clear the inputs before the request starts.
    setCredentials({ apiKey: '', secretKey: '', passphrase: '' })
    setSaveCredentials(false)
    setConnectError(null)
    setAccountActionError(null)
    setNotice(null)
    if (!body.api_key || !body.secret_key || !body.passphrase) {
      setConnectError('请填写 API Key、Secret 和 Passphrase。提交后密钥输入框会清空。')
      return
    }

    setConnectPending(true)
    try {
      const result = await cryptoApi.demoConnect(exchange, body)
      const updatedAccount = result.account
      queryClient.setQueryData<DemoAccountsResult>(QK.demoAccounts, previous => {
        const accounts = previous?.accounts ?? EXCHANGES.map(item => ({
          exchange: item,
          site: 'global' as DemoSite,
          account_mode: 'classic' as DemoAccountMode,
          configured: false,
          saved: false,
          masked_key: '',
          state: 'unconfigured' as const,
        }))
        return {
          accounts: accounts.map(item => item.exchange === updatedAccount.exchange ? updatedAccount : item),
          persistence_supported: previous?.persistence_supported ?? false,
          readonly: true,
        }
      })
      const snapshotKey = QK.demoSnapshots(exchange)
      await queryClient.cancelQueries({ queryKey: snapshotKey })
      queryClient.setQueryData(QK.demoSnapshot(exchange, updatedAccount.site), result.snapshot)
      if (isCurrentAction()) setNotice('官方模拟账户连接成功，账户数据为只读展示。')
    } catch (error) {
      if (isCurrentAction()) setConnectError(safeConnectError(error, exchange, body.account_mode))
      void queryClient.invalidateQueries({ queryKey: QK.demoAccounts })
    } finally {
      if (isCurrentAction()) {
        setCredentials({ apiKey: '', secretKey: '', passphrase: '' })
        setSaveCredentials(false)
      }
      setConnectPending(false)
    }
  }

  const handleDisconnect = async () => {
    if (actionPending) return
    const actionGeneration = actionContext.current.generation
    const isCurrentAction = () => actionContext.current.exchange === exchange && actionContext.current.generation === actionGeneration
    setDisconnectPending(true)
    setAccountActionError(null)
    setNotice(null)
    try {
      const result = await cryptoApi.demoDisconnect(exchange)
      queryClient.setQueryData<DemoAccountsResult>(QK.demoAccounts, previous => previous ? {
        ...previous,
        accounts: previous.accounts.map(item => item.exchange === exchange ? result.account : item),
      } : previous)
      const queryKey = QK.demoSnapshots(exchange)
      await queryClient.cancelQueries({ queryKey })
      queryClient.removeQueries({ queryKey })
      if (isCurrentAction()) {
        setSite('global')
        setAccountMode(defaultDemoAccountMode(exchange))
        setCredentials({ apiKey: '', secretKey: '', passphrase: '' })
        setSaveCredentials(false)
        setDisconnectOpen(false)
        setNotice('本机官方模拟连接已清除；交易所 API Key 与官方模拟账户数据未更改。')
      }
    } catch (error) {
      if (isCurrentAction()) setAccountActionError(error instanceof Error && error.message.includes('内存会话已断开')
        ? '内存会话已断开，但本机保存配置未能清除。请检查本机配置文件后重试。' : '断开失败，请稍后重试。')
      void queryClient.invalidateQueries({ queryKey: QK.demoAccounts })
    } finally {
      setDisconnectPending(false)
    }
  }

  const refresh = () => {
    void quoteQuery.refetch()
    void candlesQuery.refetch()
    if (configured) void snapshotQuery.refetch()
  }

  const quote = quoteQuery.data
  const snapshotNeedsAttention = !!snapshot && (snapshot.stale || !!snapshot.error || snapshotQuery.isError || snapshotQuery.isFetching)
  const snapshotMessage = snapshotQuery.isFetching
    ? '正在刷新；表格暂时保留最近一次成功快照。'
    : snapshotQuery.isError
      ? '账户刷新失败，当前展示的是最近一次成功快照。'
      : (snapshot?.error || snapshot?.stale)
        ? '账户刷新失败或快照已过期；原始错误详情已屏蔽。'
        : ''

  return <div className="space-y-4 p-4 md:p-6">
    <PageHeader
      title={`${exchangeName(exchange)} 官方模拟账户`}
      subtitle="独立于本地模拟策略"
      titleExtra={<ShieldCheck className="h-4 w-4 text-accent" />}
      right={<Link to="/crypto-paper" className="inline-flex shrink-0 items-center gap-1 rounded-btn border border-border px-3 py-2 text-xs text-secondary transition-colors hover:border-accent hover:text-accent"><ArrowLeft className="h-3.5 w-3.5" />返回数字资产模拟</Link>}
      className="flex-wrap pl-10 pr-0 md:pl-0"
    />

    <div className="rounded-card border-2 border-accent/50 bg-accent/10 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/15 px-3 py-1 text-xs font-semibold text-accent"><Eye className="h-3.5 w-3.5" />官方模拟账户</span>
        <span className="text-xs text-muted">账户表格只读；模拟交易控制独立且默认关闭。</span>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-secondary">账户表格仅展示交易所返回的数据，不展示虚构余额。下方可单独启用官方模拟交易；不会自动连接、启用或修改已有本地策略账户。</p>
      {exchange === 'okx' && <a href="#okx-strategy-bots" className="mt-3 inline-flex rounded-btn border border-accent/40 px-3 py-2 text-xs font-medium text-accent">查看三套 10 USDT 自动模拟策略</a>}
    </div>

    <section className="rounded-card border border-border bg-surface p-3 md:p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-foreground">公开市场行情</h2>
          <p className="mt-1 text-[11px] leading-relaxed text-muted">图表与报价来自公开市场，仅供观察；官方模拟账户的成交与标记价格以账户快照为准，两者可能不同。</p>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:flex">
          <label className="text-[11px] text-muted">交易所
            <select aria-label="官方模拟交易所" value={exchange} onChange={event => selectExchange(event.target.value as DemoExchange)} disabled={actionPending} className="mt-1 block w-full rounded-btn border border-border bg-base px-2 py-2 text-xs text-foreground sm:ml-2 sm:mt-0 sm:inline-block sm:w-auto disabled:cursor-not-allowed disabled:opacity-60">
              {EXCHANGES.map(item => <option key={item} value={item}>{exchangeName(item)}</option>)}
            </select>
          </label>
          <label className="text-[11px] text-muted">交易对
            <select aria-label="官方行情交易对" value={symbol} onChange={event => setSymbol(event.target.value as CryptoSymbol)} className="mt-1 block w-full rounded-btn border border-border bg-base px-2 py-2 text-xs text-foreground sm:ml-2 sm:mt-0 sm:inline-block sm:w-auto">
              {SYMBOLS.map(item => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label className="text-[11px] text-muted">周期
            <select aria-label="官方行情周期" value={interval} onChange={event => setInterval(event.target.value as CryptoChartInterval)} className="mt-1 block w-full rounded-btn border border-border bg-base px-2 py-2 text-xs text-foreground sm:ml-2 sm:mt-0 sm:inline-block sm:w-auto">
              {INTERVALS.map(item => <option key={item} value={item}>{INTERVAL_LABELS[item]}</option>)}
            </select>
          </label>
          <button type="button" onClick={refresh} className={`${BUTTON_CLASS} col-span-2 sm:self-end`}><RefreshCcw className="h-3.5 w-3.5" />刷新</button>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="rounded-btn border border-border bg-base p-2"><div className="text-[11px] text-muted">买一</div><div className="mt-1 break-all font-mono text-sm text-foreground">{showValue(quote?.bid)}</div></div>
        <div className="rounded-btn border border-border bg-base p-2"><div className="text-[11px] text-muted">卖一</div><div className="mt-1 break-all font-mono text-sm text-foreground">{showValue(quote?.ask)}</div></div>
        <div className="rounded-btn border border-border bg-base p-2"><div className="text-[11px] text-muted">标记价格</div><div className="mt-1 break-all font-mono text-sm text-foreground">{showValue(quote?.mark)}</div></div>
        <div className="rounded-btn border border-border bg-base p-2"><div className="text-[11px] text-muted">行情时间</div><div className="mt-1 text-xs text-foreground">{showTime(quote?.asof_ms)}</div></div>
      </div>
      {quoteQuery.isPending && <p className="mt-2 text-xs text-muted">正在加载公开报价…</p>}
      {quoteQuery.isError && <p role="status" className="mt-2 text-xs text-amber-300">公开报价暂不可用；图表和账户状态仍可单独查看。</p>}
      {candlesQuery.isPending && <p className="mt-2 text-xs text-muted">正在加载历史 K 线…</p>}
      {candlesQuery.isError && <p role="status" className="mt-2 text-xs text-amber-300">历史 K 线暂不可用；已加载图表数据会保留。</p>}
      <p className="mt-2 text-[11px] text-muted">{candlesQuery.data?.closed_only ? '仅显示已收盘 K 线。' : 'K 线仅供观察。'}图表不代表任何账户持仓或策略成交。</p>
      <CryptoMarketChart key={`${exchange}:${activeSite}`} bars={candlesQuery.data?.bars ?? []} symbol={symbol} interval={interval} market="usdm" />
    </section>

    <section className="rounded-card border border-border bg-surface p-3 md:p-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-foreground">官方模拟账户连接</h2>
          <p className="mt-1 text-xs leading-relaxed text-muted">只输入在 {exchangeName(exchange)} 官方模拟模式下创建的专用 API 凭据。请先授予只读权限；不要发送到聊天、截图或工单。</p>
        </div>
        {configured && <button type="button" onClick={() => setDisconnectOpen(true)} disabled={actionPending} className={BUTTON_CLASS}><Unplug className="h-3.5 w-3.5" />断开本机连接</button>}
      </div>

      {accountsQuery.isPending && <p className="mt-3 text-xs text-muted">正在检查本机连接状态…</p>}
      {accountsQuery.isError && <div className="mt-3 rounded-btn border border-danger/30 bg-danger/5 p-3 text-xs text-danger">无法读取官方模拟账户状态。连接表单暂不可用，请稍后重试。</div>}

      {configured && accountStatus && <div className="mt-3 rounded-btn border border-accent/30 bg-accent/5 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex rounded-full border border-accent/30 bg-accent/10 px-2.5 py-1 text-xs font-medium text-accent">{stateLabel(accountStatus.state)}</span>
          <span className="text-xs text-secondary">{exchangeName(exchange)} · {accountStatus.site.toUpperCase()} · {accountStatus.account_mode.toUpperCase()}</span>
          {accountStatus.masked_key && <code className="max-w-full break-all rounded bg-base px-2 py-1 text-[11px] text-muted">{accountStatus.masked_key}</code>}
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-muted">此连接只读取官方模拟账户。断开仅清除本站连接配置，不会撤销交易所 Key 或改变交易所模拟数据。</p>
      </div>}

      {notice && <p role="status" className="mt-3 rounded-btn border border-accent/25 bg-accent/5 p-3 text-xs text-accent">{notice}</p>}
      {accountActionError && <p role="alert" className="mt-3 rounded-btn border border-danger/25 bg-danger/5 p-3 text-xs text-danger">{accountActionError}</p>}

      {!configured && !accountsQuery.isPending && !accountsQuery.isError && <form autoComplete="off" onSubmit={event => { void handleConnect(event) }} className="mt-4 space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {exchange === 'okx'
            ? <label className="text-xs text-secondary">OKX 站点
              <select aria-label="OKX 站点" value={site} onChange={event => setSite(event.target.value as DemoSite)} className={INPUT_CLASS} disabled={actionPending}>
                <option value="global">Global</option><option value="us">US</option><option value="eea">EEA</option>
              </select>
              <span className="mt-1 block text-[11px] text-muted">选择与你的 OKX 注册地区对应的站点。</span>
            </label>
            : <label className="text-xs text-secondary">Bitget 账户模式
              <select aria-label="Bitget 账户模式" value={accountMode} onChange={event => setAccountMode(event.target.value as DemoAccountMode)} className={INPUT_CLASS} disabled={actionPending}>
                <option value="uta">UTA（推荐）</option><option value="classic">Classic（手动选择）</option>
              </select>
              <span className="mt-1 block text-[11px] text-muted">新连接默认推荐 UTA；仅当模拟账户实际使用 Classic 时手动选择。连接失败不会自动切换模式。</span>
            </label>}
          <label className="text-xs text-secondary">API Key
            <input aria-label="API Key" type="password" autoComplete="new-password" spellCheck={false} value={credentials.apiKey} onChange={event => setCredentials(current => ({ ...current, apiKey: event.target.value }))} className={INPUT_CLASS} disabled={actionPending} />
          </label>
          <label className="text-xs text-secondary">Secret
            <input aria-label="Secret" type="password" autoComplete="new-password" spellCheck={false} value={credentials.secretKey} onChange={event => setCredentials(current => ({ ...current, secretKey: event.target.value }))} className={INPUT_CLASS} disabled={actionPending} />
          </label>
          <label className="text-xs text-secondary">Passphrase
            <input aria-label="Passphrase" type="password" autoComplete="new-password" spellCheck={false} value={credentials.passphrase} onChange={event => setCredentials(current => ({ ...current, passphrase: event.target.value }))} className={INPUT_CLASS} disabled={actionPending} />
          </label>
        </div>

        <label className="flex items-start gap-2 rounded-btn border border-border bg-base p-3 text-xs text-secondary">
          <input type="checkbox" aria-label="在本机加密保存密钥" checked={saveCredentials} disabled={!persistenceSupported || actionPending} onChange={event => setSaveCredentials(event.target.checked)} className="mt-0.5 accent-[var(--color-accent)]" />
          <span><span className="font-medium text-foreground">在本机加密保存密钥（可选）</span><span className="mt-1 block text-[11px] leading-relaxed text-muted">默认关闭。成功连接时若未勾选，会改用本机会话连接并替换该交易所之前保存的 Key；只有勾选且后端支持时才请求本机加密保存。</span></span>
        </label>

        <div className="flex flex-wrap items-center gap-2">
          <button type="submit" disabled={actionPending || accountsQuery.isError} className="inline-flex min-h-9 items-center justify-center rounded-btn bg-accent px-4 py-2 text-xs font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">{connectPending ? '正在连接…' : '连接官方模拟账户'}</button>
          <button type="button" disabled={actionPending} onClick={() => { setCredentials({ apiKey: '', secretKey: '', passphrase: '' }); setSaveCredentials(false); setConnectError(null) }} className={BUTTON_CLASS}>取消并清空</button>
          <span className="text-[11px] text-muted">前端不写入浏览器存储；提交、取消或切换交易所都会清空三个输入框。</span>
        </div>
        {connectError && <p role="alert" className="rounded-btn border border-danger/25 bg-danger/5 p-3 text-xs text-danger">{connectError}</p>}
      </form>}

      <details className="mt-4 rounded-btn border border-border bg-base p-3">
        <summary className="cursor-pointer text-xs font-medium text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">官方模拟模式连接步骤与文档</summary>
        <ol className="mt-3 list-decimal space-y-2 pl-5 text-xs leading-relaxed text-muted">
          <li>先在 {exchangeName(exchange)} 官网切换到模拟交易环境，再在模拟模式的 API 管理页面创建专用 Demo Key。</li>
          <li>只查看时授予读取权限；下方模拟执行另需模拟专用 Key 的交易权限，默认关闭。不要授予提现权限。</li>
          <li>仅把 Key、Secret 和 Passphrase 输入本页表单。连接请求发送到本站服务，不要通过聊天、截图或工单传递密钥。</li>
          {exchange === 'okx' && <li>OKX 用户按注册地区选择 Global、US 或 EEA 站点；不确定时先核对官方区域说明。</li>}
          {exchange === 'bitget' && <li>Bitget 新连接默认推荐 UTA；若模拟账户实际使用 Classic，请手动选择 Classic。连接失败不会自动尝试另一模式。</li>}
        </ol>
        <div className="mt-3 flex flex-wrap gap-3 text-xs">
          <a href={exchange === 'okx' ? OKX_DOCS : activeAccountMode === 'uta' ? BITGET_UTA_DOCS : BITGET_CLASSIC_DOCS} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2">{exchangeName(exchange)}{exchange === 'bitget' ? ` ${activeAccountMode.toUpperCase()}` : ''} 官方 API / 模拟交易文档</a>
        </div>
      </details>
    </section>

    <OfficialDemoTradingPanel key={`${exchange}:${configured}`} exchange={exchange} configured={configured} />
    {exchange === 'okx' && <OfficialDemoBotsPanel />}

    <section className="rounded-card border border-border bg-surface p-3 md:p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">账户数据 · 只读</h2>
          <p className="mt-1 text-xs text-muted">仅活动官方页对已配置的当前交易所请求私有账户数据；未连接时不会请求。</p>
        </div>
        {configured && <div className="text-right text-[11px] text-muted">
          <div>{snapshot ? `快照时间 ${snapshotTime(snapshot.observed_at)}` : '等待账户快照'}</div>
          {snapshot && <div className="mt-1">刷新周期 30 秒</div>}
        </div>}
      </div>

      {accountsQuery.isPending && <p className="mt-3 text-sm text-muted">正在读取本机连接状态…</p>}
      {accountsQuery.isError && <div role="status" className="mt-3 rounded-btn border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-300">账户状态暂不可用。为避免把未知状态误当成未连接，当前不展示账户数据。</div>}
      {!accountsQuery.isPending && !accountsQuery.isError && !configured && <div className="mt-3 rounded-btn border border-dashed border-border px-4 py-8 text-center text-sm text-muted">尚未连接 {exchangeName(exchange)} 官方模拟账户。公开行情仍可查看，账户数据不会请求或填入模拟数值。</div>}
      {configured && snapshotQuery.isPending && !snapshot && <p className="mt-3 text-sm text-muted">正在从官方模拟环境获取只读账户快照…</p>}
      {configured && snapshotQuery.isError && !snapshot && <div role="alert" className="mt-3 rounded-btn border border-danger/30 bg-danger/5 p-3 text-xs text-danger">账户快照获取失败。请核对连接状态，或稍后刷新。</div>}
      {snapshot && snapshotNeedsAttention && <div role="status" className="mt-3 rounded-btn border border-amber-500/35 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-300">
        {snapshotMessage || '账户快照已标记为旧数据。'}
      </div>}
      {configured && !snapshot && !snapshotQuery.isPending && !snapshotQuery.isError && <div className="mt-3 rounded-btn border border-dashed border-border px-4 py-6 text-center text-sm text-muted">暂时没有账户快照数据。</div>}

      <div className="mt-4 border-b border-border">
        <div role="tablist" aria-label="官方模拟账户数据" className="flex max-w-full gap-1 overflow-x-auto">
          {TABS.map(tab => <button key={tab.id} type="button" role="tab" aria-selected={activeTab === tab.id} onClick={() => setActiveTab(tab.id)} className={`shrink-0 border-b-2 px-3 py-2 text-xs transition-colors ${activeTab === tab.id ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-secondary'}`}>
            {tab.label}{activeTab === tab.id && snapshot ? ` (${tab.id === 'balances' ? snapshot.balances?.length ?? 0 : tab.id === 'positions' ? snapshot.positions?.length ?? 0 : tab.id === 'orders' ? snapshot.orders?.length ?? 0 : snapshot.fills?.length ?? 0})` : ''}
          </button>)}
        </div>
      </div>
      <div role="tabpanel" aria-label={TABS.find(tab => tab.id === activeTab)?.label} className="mt-3">
        {tabContent(activeTab, snapshot, configured)}
      </div>
    </section>

    <ConfirmDialog open={disconnectOpen} pending={disconnectPending} onCancel={() => setDisconnectOpen(false)} onConfirm={() => { void handleDisconnect() }} />
  </div>
}
