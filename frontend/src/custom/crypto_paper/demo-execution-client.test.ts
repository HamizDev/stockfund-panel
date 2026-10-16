import { afterEach, expect, it, vi } from 'vitest'
import { demoExecutionApi } from './demo-execution-client'

const status = {
  armed: false, auto: false, source_account_id: null, symbol: 'BTCUSDT', max_notional: 10,
  daily_loss_limit: 5, reason: '', managed: true, decisions: [], orders: [],
}
const plan = {
  id: 'plan/1', expires_at_ms: 1_800_000_000_000, symbol: 'BTCUSDT', action: 'open_long',
  quantity: '0.001', quantity_unit: 'BTC', estimated_notional: 10, price: '10000',
  reduce_only: false, leverage: 1, margin_mode: 'isolated', warnings: [],
}
const order = {
  client_id: 'client-1', order_id: null, state: 'uncertain', symbol: 'BTCUSDT', action: 'open_long',
  quantity: '0.001', estimated_notional: 10, created_at_ms: 1_800_000_000_000,
}

afterEach(() => vi.unstubAllGlobals())

it('uses same-origin demo endpoints and exact arm, plan, submit, and reconcile payloads', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(status), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(status), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(plan), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ order }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ order }), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)

  await demoExecutionApi.status('bitget')
  await demoExecutionApi.arm('bitget', {
    enabled: true, confirm_demo: true, symbol: 'ETHUSDT', source_account_id: 'source/1',
    max_notional: 20, daily_loss_limit: 4,
  })
  await demoExecutionApi.plan('bitget', { symbol: 'BTCUSDT', action: 'open_long', notional: 10 })
  await demoExecutionApi.submit('bitget', 'plan/1')
  await demoExecutionApi.reconcile('bitget', 'client/1')

  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    '/api/custom/crypto-paper/demo/trading/bitget',
    '/api/custom/crypto-paper/demo/trading/bitget/arm',
    '/api/custom/crypto-paper/demo/trading/bitget/plan',
    '/api/custom/crypto-paper/demo/trading/bitget/submit',
    '/api/custom/crypto-paper/demo/trading/bitget/reconcile/client%2F1',
  ])
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
    enabled: true, confirm_demo: true, symbol: 'ETHUSDT', source_account_id: 'source/1',
    max_notional: 20, daily_loss_limit: 4,
  })
  expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ symbol: 'BTCUSDT', action: 'open_long', notional: 10 })
  expect(JSON.parse(fetchMock.mock.calls[3][1].body)).toEqual({ plan_id: 'plan/1', confirm: true })
})

it('rejects malformed plan responses before exposing a submit-ready plan', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...plan, leverage: 10 }), { status: 200 })))
  await expect(demoExecutionApi.plan('okx', { symbol: 'BTCUSDT', action: 'open_long', notional: 10 })).rejects.toThrow('模拟交易计划响应格式无效')
})
