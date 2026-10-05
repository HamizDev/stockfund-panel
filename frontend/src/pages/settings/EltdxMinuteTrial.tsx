import { useMutation } from '@tanstack/react-query'
import { api } from '@/lib/api'

const TRIAL_SYMBOLS = [
  { symbol: '600519.SH', label: '股票 600519.SH' },
  { symbol: '510300.SH', label: 'ETF 510300.SH' },
] as const

function formatBeijingTime(value: string | null | undefined): string | null {
  if (!value) return null

  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
  const date = new Date(hasZone ? value : `${value}+08:00`)
  if (!Number.isFinite(date.getTime())) return value

  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date).map(part => [part.type, part.value]))

  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} 北京时间`
}

function readableError(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : '分钟试拉失败，请检查 ELTDX 网关连接后重试。'
}

export function EltdxMinuteTrial({ available }: { available: boolean }) {
  const trial = useMutation({
    mutationFn: (symbol: string) => api.testDataSource('eltdx_gateway', 'minute', [symbol]),
    retry: false,
  })
  const observedEnd = formatBeijingTime(trial.data?.observed_end)
  const hasRows = (trial.data?.rows ?? 0) > 0

  return (
    <section className="mt-4 rounded-lg border border-border/60 bg-elevated/20 p-3" aria-label="ELTDX 分钟试拉">
      <div className="mb-1 text-xs font-medium text-foreground">分钟数据试拉</div>
      <p className="mb-3 text-[11px] leading-relaxed text-muted">
        单只按需验证 1 分钟数据；结果只用于检查，不会保存分钟数据或切换数据源。
      </p>
      <div className="flex flex-wrap gap-2">
        {TRIAL_SYMBOLS.map(({ symbol, label }) => {
          const pending = trial.isPending && trial.variables === symbol
          return (
            <button
              key={symbol}
              type="button"
              aria-label={`试拉${label}`}
              onClick={() => trial.mutate(symbol)}
              disabled={!available || trial.isPending}
              className="inline-flex items-center gap-1 rounded-btn bg-elevated px-3 py-1.5 text-xs text-secondary transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
            >
              {pending ? '试拉中...' : `试拉${label}`}
            </button>
          )
        })}
      </div>
      {!available && (
        <p className="mt-2 text-[11px] text-warning/80">插件依赖未就绪，暂不可试拉。</p>
      )}
      {trial.isError && (
        <p className="mt-3 text-xs text-danger" role="alert">试拉失败：{readableError(trial.error)}</p>
      )}
      {trial.isSuccess && trial.data && (
        <div className="mt-3 space-y-1 rounded-lg border border-border/60 bg-elevated/20 px-3 py-2 text-xs" role="status">
          <div><span className={hasRows ? 'text-accent font-medium' : 'text-warning font-medium'}>{hasRows ? `试拉完成 · ${trial.data.rows} 根` : '未返回分钟数据，请核对网关与证券覆盖'}</span></div>
          {observedEnd && <div className="text-secondary">实际数据截止：{observedEnd}</div>}
          {trial.data.price_basis && <div className="text-secondary">价格口径：{trial.data.price_basis === 'raw' ? '不复权原始价' : trial.data.price_basis}</div>}
          {trial.data.volume_unit && <div className="text-secondary">成交量单位：{trial.data.volume_unit}</div>}
          <div className="text-secondary">
            成交额：{trial.data.amount_available === true ? '可用' : '不可用'}
          </div>
          {trial.data.amount_available !== true && (
            <div className="text-muted">成交额单位尚未核实，不用于计算或验证均价。</div>
          )}
        </div>
      )}
    </section>
  )
}
