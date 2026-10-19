import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { demoBotsApi, type DemoBotsStatus } from './demo-bots-client'

function validStatus(): DemoBotsStatus {
  return {
    exchange: 'okx', environment: 'demo', connection_ready: true, legacy_armed: false,
    bots: [{
      id: 'btc-breakout', name: 'BTC 突破', symbol: 'BTCUSDT', strategy_id: 'channel_breakout',
      description: '15 分钟突破规则', interval: '15m', budget_usdt: 10, max_notional: 9,
      enabled: false, armed: false, status: 'paused', reason: '已准备；连接欧易官方模拟后可启用',
      last_check_ms: null, last_bar_ms: null, last_signal: null, position: null,
      metrics: {
        closed_trades: 0, wins: 0, win_rate_pct: null, realized_pnl_usdt: '0', fees_usdt: '0',
        open_pnl_usdt: '0', allocation_equity_usdt: '10', max_drawdown_pct: 0, funding_included: false,
      },
      orders: [],
    }],
  }
}

let fetchMock: ReturnType<typeof vi.fn>

function reply(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue(reply(validStatus()))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => vi.unstubAllGlobals())

it('reads and validates the OKX demo bot status using the shared crypto request helper', async () => {
  const controller = new AbortController()
  const result = await demoBotsApi.status('okx', controller.signal)
  expect(result.bots[0]?.description).toBe('15 分钟突破规则')
  expect(fetchMock).toHaveBeenCalledWith('/api/custom/crypto-paper/demo/bots/okx', expect.objectContaining({
    credentials: 'same-origin', signal: controller.signal,
  }))
})

it('uses the documented configuration, enable, and reconciliation endpoints and request bodies', async () => {
  await demoBotsApi.createPresets('okx')
  await demoBotsApi.setEnabled('okx', { enabled: true, confirm_demo: true })
  await demoBotsApi.setBotEnabled('okx', 'btc / demo', { enabled: false, confirm_demo: false })
  await demoBotsApi.reconcile('okx', 'btc / demo')

  expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
    '/api/custom/crypto-paper/demo/bots/okx/presets',
    '/api/custom/crypto-paper/demo/bots/okx/enabled',
    '/api/custom/crypto-paper/demo/bots/okx/btc%20%2F%20demo/enabled',
    '/api/custom/crypto-paper/demo/bots/okx/btc%20%2F%20demo/reconcile',
  ])
  const bodies = fetchMock.mock.calls.map(call => (call[1] as RequestInit).body)
  expect(bodies).toEqual([
    '{}',
    JSON.stringify({ enabled: true, confirm_demo: true }),
    JSON.stringify({ enabled: false, confirm_demo: false }),
    '{}',
  ])
})

it('rejects a malformed status without forwarding an arbitrary response detail', async () => {
  fetchMock.mockResolvedValueOnce(reply({ detail: 'secret diagnostic payload' }))
  await expect(demoBotsApi.status('okx')).rejects.toThrow('官方模拟策略状态响应格式无效')
})

it('preserves boolean restart policy and rejects an invalid permission value', async () => {
  const good = validStatus()
  good.bots[0].resume_on_restart = true
  fetchMock.mockResolvedValueOnce(reply(good))
  expect((await demoBotsApi.status('okx')).bots[0].resume_on_restart).toBe(true)
  fetchMock.mockResolvedValueOnce(reply({ ...good, bots: [{ ...good.bots[0], resume_on_restart: 'true' }] }))
  await expect(demoBotsApi.status('okx')).rejects.toThrow('官方模拟策略状态响应格式无效')
})

it('preflight uses GET and rejects malformed configuration without echoing it', async () => {
  fetchMock.mockResolvedValueOnce(reply({ private: 'do-not-render' }))
  await expect(demoBotsApi.preflight('okx')).rejects.toThrow('官方模拟检查响应格式无效')
  expect(fetchMock.mock.calls[0][0]).toBe('/api/custom/crypto-paper/demo/bots/okx/preflight')
  expect(fetchMock.mock.calls[0][1].method).toBeUndefined()
})
