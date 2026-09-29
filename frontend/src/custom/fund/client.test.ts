import { afterEach, describe, expect, it, vi } from 'vitest'
import { fundApi } from './client'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fund API errors', () => {
  it('shows the backend reason for a business 503 without retrying', async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ detail: '扶摇未配置，且本机基金服务不可用' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } },
    ))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fundApi.nav('011370.OF')).rejects.toThrow('扶摇未配置，且本机基金服务不可用')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('reads fragmented NDJSON AI events', async () => {
    const encoder = new TextEncoder()
    const chunks = [
      '{"type":"meta","candidates":[]}',
      '\n{"type":"del',
      'ta","content":"分析"}\n{"type":"done"}',
    ]
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new ReadableStream({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)))
        controller.close()
      },
    }), { status: 200 })))

    const events = []
    for await (const event of fundApi.aiPickStream({ fund_type: '混合型', horizon: '1y', share: 'all' })) {
      events.push(event)
    }
    expect(events.map((event) => event.type)).toEqual(['meta', 'delta', 'done'])
    expect(events[1].content).toBe('分析')
  })
})
