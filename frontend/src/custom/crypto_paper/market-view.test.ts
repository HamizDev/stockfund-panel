import { describe, expect, it } from 'vitest'
import { positionLevels, validQuote, validTicker } from './market-view'
import type { CryptoStrategyAccount } from './client'

describe('Bitget market view', () => {
  it('validates REST fallback identity, age and prices independently', () => {
    const quote = { exchange: 'bitget', market: 'usdm', symbol: 'ETHUSDT', bid: '100', ask: '101', mark: '100.5', asof_ms: 100_000 }
    expect(validQuote(quote, 'ETHUSDT', 100_000)).toBe(true)
    expect(validQuote(quote, 'ETHUSDT', 161_000)).toBe(false)
    expect(validQuote({ ...quote, exchange: 'binance' }, 'ETHUSDT', 100_000)).toBe(false)
    expect(validQuote({ ...quote, symbol: 'BTCUSDT' }, 'ETHUSDT', 100_000)).toBe(false)
    expect(validQuote({ ...quote, mark: null }, 'ETHUSDT', 100_000)).toBe(false)
    expect(validQuote({ ...quote, bid: 'Infinity' }, 'ETHUSDT', 100_000)).toBe(false)
  })
  it('requires matching symbol and fresh finite ordered prices', () => {
    const ticker = { symbol: 'ETHUSDT', bid: '100', ask: '101', last: '100.5', mark: '100.6', asof_ms: 100_000 }
    expect(validTicker(ticker, 'ETHUSDT', 100_000)).toBe(true)
    expect(validTicker(ticker, 'BTCUSDT', 100_000)).toBe(false)
    expect(validTicker(ticker, 'ETHUSDT', 116_000)).toBe(false)
    expect(validTicker({ ...ticker, ask: '99' }, 'ETHUSDT', 100_000)).toBe(false)
    expect(validTicker({ ...ticker, mark: 'NaN' }, 'ETHUSDT', 100_000)).toBe(false)
    expect(validTicker({ ...ticker, mark: null }, 'ETHUSDT', 100_000)).toBe(false)
  })
  it('uses price percentages for long and short, without multiplying leverage', () => {
    const account = { exchange: 'bitget', market: 'usdm', symbol: 'ETHUSDT', leverage: 20, stop_loss_pct: 2, take_profit_pct: 4, positions: { ETHUSDT: { side: 'long', entry: '100' } } } as unknown as CryptoStrategyAccount
    expect(positionLevels(account).map(level => level.price)).toEqual([100, 98, 104])
    account.positions.ETHUSDT = { side: 'short', entry: '100' }
    expect(positionLevels(account).map(level => level.price)).toEqual([100, 102, 96])
    account.exchange = 'binance'
    expect(positionLevels(account)).toEqual([])
  })
  it('never invents risk lines for missing positions or nonfinite entry', () => {
    const account = { exchange: 'bitget', market: 'usdm', symbol: 'ETHUSDT', stop_loss_pct: 2, take_profit_pct: 4, positions: {} } as unknown as CryptoStrategyAccount
    expect(positionLevels(account)).toEqual([])
    account.positions.ETHUSDT = { side: 'long', entry: 'Infinity' }
    expect(positionLevels(account)).toEqual([])
  })
})
