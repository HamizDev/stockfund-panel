/**
 * 日K查询配置 — klineDaily 的唯一权威 options。
 *
 * StockDailyKChart(图表) 与 StockPanel(信息条) 各自 useQuery 共享同一 cache key,
 * React Query 按 key 去重只发一次请求; 邻近预取 prefetchQuery 也复用本配置, 三处不会漂移。
 *
 * placeholderData 内置"仅同 symbol 占位"守卫: 改日期范围/扩展字段时旧数据可暂显(不闪),
 * 切股时不透传上一只股票的数据(不误显示)。
 */
import { api, type KlineDailyLatestResponse, type KlineDailyResponse } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

/** 分时 tab 多日分时默认周期 (StockPanel 预取与弹窗存储回退共用, 避免魔数两处漂移) */
export const DEFAULT_INTRADAY_DAYS = 10

export function klineDailyQueryOptions(
  symbol: string,
  dateRange: { start: string; end: string },
  extColumns?: string,
) {
  return {
    queryKey: QK.kline(symbol, dateRange.start, dateRange.end, extColumns),
    queryFn: () => api.klineDaily(symbol, undefined, dateRange, extColumns),
    // 工厂无 TData 泛型, 参数用 any 以便 useQuery/prefetchQuery 共用
    placeholderData: (prev: any, prevQuery: any) => {
      const prevKey = prevQuery?.queryKey as readonly unknown[] | undefined
      return prevKey?.[1] === symbol ? prev : undefined
    },
  }
}

export function klineDailyLatestQueryOptions(symbol: string) {
  return {
    queryKey: QK.klineLatest(symbol),
    queryFn: () => api.klineDailyLatest(symbol),
  }
}

export function mergeLatestKlineRow(
  current: KlineDailyResponse | undefined,
  latest: KlineDailyLatestResponse,
): KlineDailyResponse | undefined {
  if (!current || !latest.row || current.symbol !== latest.symbol) return current

  const latestDate = String(latest.row.date).slice(0, 10)
  const last = current.rows.at(-1)
  if (!last) return { ...current, rows: [{ ...latest.row, date: latestDate }] }

  const lastDate = String(last.date).slice(0, 10)
  if (latestDate < lastDate) return current
  if (latestDate === lastDate) {
    return {
      ...current,
      rows: [...current.rows.slice(0, -1), { ...last, ...latest.row, date: latestDate }],
    }
  }
  return { ...current, rows: [...current.rows, { ...latest.row, date: latestDate }] }
}

/**
 * 单日分时查询配置 — 与 klineDailyQueryOptions 同风格的单源 options (date 为空 = 最新日内)。
 *
 * live 仅当日盘中生效: 传 true 时后端实时拉取最新K, 不被分钟增量落盘(≥60s 一轮)拖慢;
 * 历史日期后端自行忽略 live, 故多日图与预取恒传 true 亦不影响历史读取。
 */
export function klineMinuteQueryOptions(symbol: string, date?: string, live?: boolean) {
  return {
    queryKey: QK.klineMinute(symbol, date ?? '', live),
    queryFn: ({ signal }: { signal?: AbortSignal } = {}) => api.klineMinute(symbol, date ?? undefined, live, signal),
  }
}

/** 收盘分钟标签: 连续竞价全天最后一根分钟K的时间。 */
const CLOSE_MINUTE = '15:00'

/** 全天真正定版时刻: 15:00 收盘后仍可按收盘价成交 (盘后固定价) 至 15:30,
 * 其间成交量/额仍可能变化 —— 与后端 quote_service 定版重试窗口终点
 * (15:30) 同口径; 盘后管道默认 15:35 在此之后启动。 */
const MARKET_ALL_OVER = '15:30'

/** 北京时间今日 YYYY-MM-DD (与后端 cn_today 同口径; 不用 UTC toISOString, 避免傍晚错日)。 */
export function cnToday(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date())
}

/** 北京时间此刻 'HH:MM'。 */
function cnNowHHMM(): string {
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date())
}

interface CnClock {
  year: number
  month: number
  day: number
  hour: number
  minute: number
  second: number
}

function cnClock(now = new Date()): CnClock {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai',
    calendar: 'gregory',
    numberingSystem: 'latn',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now)
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return {
    year: Number(values.year),
    month: Number(values.month),
    day: Number(values.day),
    hour: Number(values.hour),
    minute: Number(values.minute),
    second: Number(values.second),
  }
}

function cnWallTimeMs(clock: CnClock): number {
  return Date.UTC(clock.year, clock.month - 1, clock.day, clock.hour, clock.minute, clock.second)
}

function delayUntilCnTime(hour: number, minute: number, clock = cnClock()): number {
  const target = Date.UTC(clock.year, clock.month - 1, clock.day, hour, minute)
  return Math.max(1_000, target - cnWallTimeMs(clock))
}

/** 周末/收盘后唤醒 latest 查询时跳过周末；不在前端推断节假日。 */
function nextCnWeekdayOpenDelay(clock = cnClock()): number {
  const nowMs = cnWallTimeMs(clock)
  const weekday = new Date(Date.UTC(clock.year, clock.month - 1, clock.day)).getUTCDay()
  const minutes = clock.hour * 60 + clock.minute
  const dayOffset = weekday === 0 || weekday === 6 || minutes >= 9 * 60 + 30 ? 1 : 0

  let target = Date.UTC(clock.year, clock.month - 1, clock.day + dayOffset, 9, 30)
  while ([0, 6].includes(new Date(target).getUTCDay())) target += 24 * 60 * 60 * 1000
  return Math.max(1_000, target - nowMs)
}

/** 休盘期间暂停请求，下一次检查安排在午后或下个北京时间工作日开盘。 */
function cnMarketPauseDelay(clock = cnClock()): number | null {
  const weekday = new Date(Date.UTC(clock.year, clock.month - 1, clock.day)).getUTCDay()
  const minutes = clock.hour * 60 + clock.minute
  if (weekday === 0 || weekday === 6) return nextCnWeekdayOpenDelay(clock)
  if (minutes < 9 * 60 + 30) return delayUntilCnTime(9, 30, clock)
  if (minutes >= 11 * 60 + 30 && minutes < 13 * 60) return delayUntilCnTime(13, 0, clock)
  if (minutes >= 15 * 60 + 30) return nextCnWeekdayOpenDelay(clock)
  return null
}

/** 跟随最新日线时按市场时段刷新，休盘时仅在下一次工作日开盘唤醒。 */
export function marketSessionRefetchInterval(ms?: number) {
  return (_query: any): number | false => {
    if (ms == null) return false
    return cnMarketPauseDelay() ?? ms
  }
}

/** 行 datetime ('YYYY-MM-DD HH:MM:SS' 北京墙钟契约) → 'HH:MM'; 非常规格式返回 ''。 */
function rowMinute(r: { datetime?: string } | undefined): string {
  const s = String(r?.datetime ?? '')
  return s.length >= 16 ? s.slice(11, 16) : ''
}

/**
 * 分时轮询停表 — 数据不可变时停止, 消除无效请求 (周末/盘后/翻看历史日期)。
 *
 * - 未传间隔 → 不轮询 (调用方默认关闭)
 * - 显式历史日期 → 停；latest 返回旧日期时休盘到下个工作日开盘再唤醒
 * - 当日空结果在交易时段继续轮询；休盘时等到午后或下个工作日开盘
 * - 当日: 收盘根 (≥15:00 的K) 已出现 **且** 已过 15:30 → 停。
 *   完整性**只认收盘根, 不按根数** —— 实测本系统全天 241 根 (首根 09:30
 *   竞价根 + 15:00 收盘根), 按根数阈值会在第 240 根 (14:59) 到位时误停,
 *   恰好错过 15:00 收盘根; 收盘根判据还天然兼容盘中缺根与部分交易日。
 *   而 15:00 收盘根出现后**不能立即停**: 盘后按收盘价成交可持续到 15:30,
 *   其间量/额仍可能更新 (当前数据源分钟K止于 15:00, 此窗口为防御性保留,
 *   与后端"15:30 才允许定版/重建"的边界一致)。
 * - 其余 (当日 15:30 前) → 按间隔继续
 */
export function minuteRefetchInterval(ms?: number, opts?: { retryMissingReference?: boolean }) {
  let recoveryContext = ''
  let recoveryResponses = 0
  let recoveryUpdate: string | undefined
  return (query: any): number | false => {
    if (ms == null) return false
    const queryKey = query.queryKey as readonly unknown[] | undefined
    const requestedDate = typeof queryKey?.[2] === 'string' ? queryKey[2] : ''
    const latestRequest = requestedDate === ''
    const today = cnToday()
    if (requestedDate && requestedDate !== today) return false

    const d = query.state.data
    const clock = cnClock()
    const weekday = new Date(Date.UTC(clock.year, clock.month - 1, clock.day)).getUTCDay()
    const nowMinutes = clock.hour * 60 + clock.minute
    const afterClose = nowMinutes >= 15 * 60 + 30
    const weekend = weekday === 0 || weekday === 6

    // 对今天显式选定的日期，只刷新到当天收盘；latest 要跨日保留唤醒计时器。
    if (!latestRequest && (weekend || afterClose)) return false

    if (typeof d?.date === 'string' && d.date < today) {
      return latestRequest ? cnMarketPauseDelay(clock) ?? ms : false
    }
    if (typeof d?.date === 'string' && d.date > today) return false

    const pause = cnMarketPauseDelay(clock)
    // Optional baseline fetches can finish just after 11:30. Give only the
    // selected analysis chart three recovery polls, then return to the session
    // timer. Count response updates, not repeated interval callback evaluations.
    const missingReference = d?.date === today && d?.rows?.length > 0
      && d.prev_close == null && d.prev_close_source === 'none'
    if (opts?.retryMissingReference && pause != null && !weekend
      && nowMinutes >= 9 * 60 + 30 && !afterClose && missingReference) {
      const context = `${queryKey?.[1]}:${d.date}`
      if (context !== recoveryContext) {
        recoveryContext = context
        recoveryResponses = 0
        recoveryUpdate = undefined
      }
      const update = `${query.state.dataUpdatedAt ?? 0}:${query.state.errorUpdatedAt ?? 0}`
      if (recoveryUpdate !== update) {
        recoveryUpdate = update
        recoveryResponses += 1
      }
      if (recoveryResponses <= 3) return Math.min(pause, ms)
    } else {
      recoveryContext = ''
      recoveryResponses = 0
      recoveryUpdate = undefined
    }
    if (!d || d.source === 'none') return pause ?? ms
    const closeBarArrived = (d.rows ?? []).some((r: { datetime?: string }) => rowMinute(r) >= CLOSE_MINUTE)
    if (closeBarArrived && cnNowHHMM() >= MARKET_ALL_OVER) {
      return latestRequest ? nextCnWeekdayOpenDelay(clock) : false
    }
    return pause ?? ms
  }
}

/** 多日分时查询配置 — 分时 tab 的 StockMultiDayIntradayChart 与 邻近预取 共用。
 * 内嵌「仅同 symbol 占位」守卫 (key 结构 ['kline-minute-range', symbol, days], index 1 为 symbol),
 * 与 klineDailyQueryOptions 同源, 调用点不再各自手写。 */
export function klineMinuteRangeQueryOptions(symbol: string, days: number) {
  return {
    queryKey: QK.klineMinuteRange(symbol, days),
    queryFn: () => api.klineMinuteRange(symbol, days),
    placeholderData: (prev: any, prevQuery: any) => {
      const prevKey = prevQuery?.queryKey as readonly unknown[] | undefined
      return prevKey?.[1] === symbol ? prev : undefined
    },
  }
}
