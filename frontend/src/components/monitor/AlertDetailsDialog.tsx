import { X } from 'lucide-react'
import { Modal } from '@/components/Modal'
import { fmtPct, fmtPrice } from '@/lib/format'
import type { AlertEvent } from '@/lib/api'

type RelatedSymbol = NonNullable<AlertEvent['related_symbols']>[number]

interface AlertDetailsDialogProps {
  event: AlertEvent
  sourceLabel: string
  signalLabel: (field: string) => string
  onClose: () => void
  onPreview: (event: AlertEvent) => void
}

const CN_DATE_TIME = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

function beijingDateTime(ts: number): string {
  const date = new Date(ts)
  if (!Number.isFinite(date.getTime())) return '未记录'
  const parts = CN_DATE_TIME.formatToParts(date)
  const pick = (type: Intl.DateTimeFormatPartTypes) => parts.find(part => part.type === type)?.value ?? ''
  return `${pick('year')}-${pick('month')}-${pick('day')} ${pick('hour')}:${pick('minute')}:${pick('second')}`
}

function recordedValue(value: number | null | undefined, format: (value: number) => string): string {
  return value == null || !Number.isFinite(value) ? '未记录' : format(value)
}

function readRelatedSymbols(event: AlertEvent): RelatedSymbol[] {
  const raw = event.related_symbols
  if (!Array.isArray(raw)) return []
  return raw.filter((item): item is RelatedSymbol => (
    !!item
    && typeof item === 'object'
    && typeof item.symbol === 'string'
    && item.symbol.trim().length > 0
  ))
}

function severityLabel(severity: string | undefined): string {
  if (!severity) return '未记录'
  const labels: Record<string, string> = {
    info: '提示',
    warn: '关注',
    warning: '关注',
    critical: '重要',
  }
  return labels[severity] ?? severity
}

function conditionDescription(
  condition: NonNullable<AlertEvent['conditions']>[number],
  signalLabel: (field: string) => string,
): string {
  const field = signalLabel(condition.field)
  if (condition.op === 'truth') return `${field} 为真`
  if (condition.value == null || !Number.isFinite(condition.value)) return `${field} ${condition.op} 未记录`
  return `${field} ${condition.op} ${condition.value}`
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6rem_minmax(0,1fr)] gap-3 border-b border-border/60 py-2 last:border-b-0 sm:grid-cols-[8rem_minmax(0,1fr)]">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="min-w-0 break-words text-sm text-foreground">{children}</dd>
    </div>
  )
}

export function AlertDetailsDialog({
  event,
  sourceLabel,
  signalLabel,
  onClose,
  onPreview,
}: AlertDetailsDialogProps) {
  const relatedSymbols = readRelatedSymbols(event)
  const isBatch = relatedSymbols.length > 0
  const hasSymbol = typeof event.symbol === 'string' && event.symbol.trim().length > 0
  const isLegacyStrategyBatch = !hasSymbol
    && !isBatch
    && (event.type === 'strategy' || event.source === 'strategy' || !!event.strategy_id)
  const hideSignals = isBatch || isLegacyStrategyBatch
  const conditions = event.conditions ?? []
  const logicLabel = event.logic === 'and'
    ? '满足全部条件（且）'
    : event.logic === 'or'
      ? '满足任一条件（或）'
      : '逻辑未记录'
  const message = typeof event.message === 'string' && event.message.trim()
    ? event.message
    : '未记录'

  return (
    <Modal
      onClose={onClose}
      labelledBy="alert-details-title"
      overlayClassName="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/60 p-3 backdrop-blur-sm sm:p-6"
      panelClassName="flex max-h-[92dvh] w-full max-w-2xl flex-col overflow-hidden rounded-card border border-border bg-surface shadow-xl"
    >
      <div className="flex shrink-0 items-start justify-between gap-4 border-b border-border px-4 py-3 sm:px-5">
        <div className="min-w-0">
          <h2 id="alert-details-title" className="text-base font-semibold text-foreground">监控消息详情</h2>
          <p className="mt-1 text-xs text-muted">{beijingDateTime(event.ts)}（北京时间）</p>
        </div>
        <button
          type="button"
          aria-label="关闭详情"
          onClick={onClose}
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-btn text-muted hover:bg-base hover:text-foreground"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5">
        <section aria-label="消息全文" className="rounded-card border border-border bg-base/60 p-3 sm:p-4">
          <h3 className="mb-2 text-xs font-medium text-muted">消息全文</h3>
          <p className="whitespace-pre-wrap break-words text-sm leading-6 text-foreground">{message}</p>
        </section>

        <section className="mt-4">
          <h3 className="mb-1 text-xs font-medium text-muted">触发记录</h3>
          <dl>
            <DetailRow label="来源">{sourceLabel || '未记录'}</DetailRow>
            <DetailRow label="级别">{severityLabel(event.severity)}</DetailRow>
            <DetailRow label="规则">{event.rule_name || '未记录'}</DetailRow>
            {hasSymbol && (
              <DetailRow label="标的">{event.name ? `${event.name} · ${event.symbol}` : event.symbol}</DetailRow>
            )}
            {event.strategy_id && <DetailRow label="策略 ID">{event.strategy_id}</DetailRow>}
            <DetailRow label="触发价格">{recordedValue(event.price, fmtPrice)}</DetailRow>
            <DetailRow label="触发涨跌幅">{recordedValue(event.change_pct, fmtPct)}</DetailRow>
            {!hideSignals && (
              <DetailRow label="触发信号">
                {event.signals?.length
                  ? <span className="flex flex-wrap gap-1.5">{event.signals.map(field => (
                    <span key={field} className="rounded border border-border px-2 py-0.5 text-xs">{signalLabel(field)}</span>
                  ))}</span>
                  : '未记录'}
              </DetailRow>
            )}
          </dl>
        </section>

        <section className="mt-4 rounded-card border border-border p-3 sm:p-4">
          <h3 className="text-xs font-medium text-foreground">规则条件（触发时记录）</h3>
          {conditions.length > 0 ? (
            <>
              <p className="mt-1 text-xs text-muted">{logicLabel}</p>
              <ul className="mt-2 space-y-1.5">
                {conditions.map((condition, index) => (
                  <li key={`${condition.field}-${condition.op}-${index}`} className="flex gap-2 text-sm text-foreground">
                    <span className="w-6 shrink-0 text-center text-muted">
                      {index === 0 ? '当' : event.logic === 'and' ? '且' : event.logic === 'or' ? '或' : '·'}
                    </span>
                    <span className="break-words">{conditionDescription(condition, signalLabel)}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : <p className="mt-2 text-sm text-muted">未记录</p>}
        </section>

        {(isBatch || hasSymbol) && (
          <section className="mt-4">
            <h3 className="mb-2 text-xs font-medium text-muted">标的</h3>
            <div className="flex flex-col items-start gap-2">
              {isBatch
                ? relatedSymbols.map(item => (
                  <button
                    key={item.symbol}
                    type="button"
                    onClick={() => onPreview({
                      ...event,
                      symbol: item.symbol,
                      name: item.name,
                      price: null,
                      change_pct: null,
                      signals: undefined,
                    })}
                    className="max-w-full rounded-btn border border-border bg-base px-3 py-2 text-left text-sm text-accent hover:border-accent/50"
                  >
                    <span className="break-words">{item.name || item.symbol} · {item.symbol}</span>
                    <span className="ml-2 whitespace-nowrap text-xs">查看标的日K</span>
                  </button>
                ))
                : <button
                  type="button"
                  onClick={() => onPreview(event)}
                  className="rounded-btn border border-border bg-base px-3 py-2 text-sm text-accent hover:border-accent/50"
                >
                  查看标的日K
                </button>}
            </div>
          </section>
        )}

        {isLegacyStrategyBatch && (
          <p className="mt-4 rounded-card border border-border bg-base/50 p-3 text-xs leading-5 text-muted">
            这条旧记录未保存标的代码，保留原始消息供查看。
          </p>
        )}
      </div>
    </Modal>
  )
}
