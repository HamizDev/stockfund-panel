import { describe, expect, it } from 'vitest'
import { availableDailyWindow } from './dataWindow'
import type { KronosJob } from './types'

function job(count = 184, requested = 512): KronosJob {
  return { id: 'test', state: 'failed', requested_at: '', result: null, message: '',
    request: { symbol: '510300.SH', name: '测试ETF', model: 'base', period: 'daily', lookback: requested, horizon: 20, samples: 5 },
    error: `已自动在线补取并校验；只有${count}根可用K线，所选窗口需要${requested}根。` }
}

describe('insufficient daily window retry', () => {
  it('offers a supported smaller window without changing the original request', () => {
    const value = job()
    expect(availableDailyWindow(value)).toBe(120)
    expect(value.request.lookback).toBe(512)
    expect(availableDailyWindow(job(405))).toBe(400)
    expect(availableDailyWindow(job(60))).toBe(60)
  })
  it('requires verified fresh history and never fabricates a minimum window', () => {
    const value = job()
    value.error = '只有184根可用K线，所选窗口需要512根；请缩短窗口或更新行情。'
    expect(availableDailyWindow(value)).toBeNull()
    expect(availableDailyWindow(job(59))).toBeNull()
    expect(availableDailyWindow(job(512))).toBeNull()
  })
  it('does not suggest retries for other targets, periods or errors', () => {
    expect(availableDailyWindow(undefined)).toBeNull()
    expect(availableDailyWindow({ ...job(), state: 'complete' })).toBeNull()
    expect(availableDailyWindow({ ...job(), error: '缺少最近已收盘交易日' })).toBeNull()
    const value = job()
    value.request.period = '1m'
    expect(availableDailyWindow(value)).toBeNull()
    const mismatch = job()
    mismatch.request.lookback = 240
    expect(availableDailyWindow(mismatch)).toBeNull()
  })
})
