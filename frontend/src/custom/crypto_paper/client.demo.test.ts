import { afterEach, expect, it, vi } from 'vitest'
import { cryptoApi, type DemoStatus, type DemoSnapshot } from './client'

function response(data: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => data } as Response
}

afterEach(() => vi.unstubAllGlobals())

it('uses the shared custom client prefix and the documented private account routes', async () => {
  const fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValueOnce(response({ accounts: [], persistence_supported: false, readonly: true }))
  await cryptoApi.demoAccounts()
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/demo/accounts', expect.objectContaining({ credentials: 'same-origin' }))

  const snapshot: DemoSnapshot = {
    exchange: 'okx', site: 'global', account_mode: 'classic', environment: 'demo', readonly: true,
    observed_at: '2026-10-16T00:00:00Z', stale: false, total_equity: null, equity_ccy: 'USDT',
    balances: [], positions: [], orders: [], fills: [],
  }
  fetchMock.mockResolvedValueOnce(response(snapshot))
  await cryptoApi.demoAccount('okx')
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/demo/accounts/okx?refresh=true', expect.objectContaining({ credentials: 'same-origin' }))

  const account: DemoStatus = { exchange: 'okx', site: 'global', account_mode: 'classic', configured: true, saved: false, masked_key: 'demo-****', state: 'connected' }
  fetchMock.mockResolvedValueOnce(response({ account, snapshot }))
  const credentials = { api_key: 'private-key', secret_key: 'private-secret', passphrase: 'private-passphrase', site: 'global' as const, account_mode: 'classic' as const, save: false }
  await cryptoApi.demoConnect('okx', credentials)
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/demo/accounts/okx/connect', expect.objectContaining({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(credentials),
  }))

  fetchMock.mockResolvedValueOnce(response({ account: { ...account, configured: false, saved: false, masked_key: '', state: 'unconfigured' } }))
  await cryptoApi.demoDisconnect('okx')
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/demo/accounts/okx/disconnect', expect.objectContaining({ method: 'POST' }))
})

it('keeps market query parameters scoped to exchange, site, symbol and interval', async () => {
  const fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValueOnce(response({ exchange: 'bitget', symbol: 'SOLUSDT', interval: '15m', closed_only: true, bars: [] }))
  await cryptoApi.demoCandles('bitget', 'eea', 'SOLUSDT', '15m')
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/demo/market/bitget/SOLUSDT/candles?interval=15m&site=eea', expect.any(Object))

  fetchMock.mockResolvedValueOnce(response({ exchange: 'okx', symbol: 'BTCUSDT', bid: '10', ask: '11', mark: null, asof_ms: 1 }))
  await cryptoApi.demoMarketQuote('okx', 'us', 'BTCUSDT')
  expect(fetchMock).toHaveBeenLastCalledWith('/api/custom/crypto-paper/demo/market/okx/BTCUSDT/quote?site=us', expect.any(Object))
})

it('rejects a private snapshot unless it identifies a read-only demo environment', async () => {
  const fetchMock = vi.fn().mockResolvedValue(response({ exchange: 'okx', site: 'global', environment: 'live', readonly: false }))
  vi.stubGlobal('fetch', fetchMock)
  await expect(cryptoApi.demoAccount('okx')).rejects.toThrow('官方模拟账户快照响应格式无效')
})
