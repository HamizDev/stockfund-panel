import type { CryptoQuote, CryptoStrategyAccount, CryptoTicker } from './client'

export function validQuote(value: unknown, symbol: string, now: number): value is CryptoQuote {
  if (!value || typeof value !== 'object') return false
  const quote = value as Record<string, unknown>
  if (quote.symbol !== symbol || quote.market !== 'usdm' || quote.exchange !== 'bitget' || typeof quote.asof_ms !== 'number' || !Number.isSafeInteger(quote.asof_ms) || now - quote.asof_ms < -5000 || now - quote.asof_ms > 60_000) return false
  for (const key of ['bid', 'ask', 'mark']) {
    const raw = quote[key]
    if (typeof raw !== 'string' || !raw.trim() || !Number.isFinite(Number(raw)) || Number(raw) <= 0) return false
  }
  return Number(quote.bid) <= Number(quote.ask)
}

export function validTicker(value: unknown, symbol: string, now: number): value is CryptoTicker {
  if (!value || typeof value !== 'object') return false
  const ticker = value as Record<string, unknown>
  if (ticker.symbol !== symbol || typeof ticker.asof_ms !== 'number' || !Number.isSafeInteger(ticker.asof_ms) || now - ticker.asof_ms < -5000 || now - ticker.asof_ms > 15_000) return false
  for (const key of ['bid', 'ask', 'mark', 'last']) {
    const raw = ticker[key]
    if (typeof raw !== 'string' || !raw.trim() || !Number.isFinite(Number(raw)) || Number(raw) <= 0) return false
  }
  return Number(ticker.bid) <= Number(ticker.ask)
}

export function positionLevels(account: CryptoStrategyAccount): { price: number; label: string; color: string }[] {
  if (account.exchange !== 'bitget' || account.market !== 'usdm' || account.effective_readonly) return []
  const position = account.positions[account.symbol]
  if (!position || typeof position !== 'object') return []
  const fields = position as Record<string, unknown>
  if (fields.side !== 'long' && fields.side !== 'short') return []
  const entry = typeof fields.entry === 'string' && fields.entry.trim() ? Number(fields.entry) : NaN
  const stop = account.stop_loss_pct, take = account.take_profit_pct
  if (!Number.isFinite(entry) || entry <= 0 || !Number.isFinite(stop) || stop <= 0 || !Number.isFinite(take) || take <= 0) return []
  const direction = fields.side === 'long' ? 1 : -1
  return [
    { price: entry, label: '持仓入场', color: '#60a5fa' },
    { price: entry * (1 - direction * stop / 100), label: '策略止损', color: '#f59e0b' },
    { price: entry * (1 + direction * take / 100), label: '策略止盈', color: '#a78bfa' },
  ].filter(level => Number.isFinite(level.price) && level.price > 0)
}
