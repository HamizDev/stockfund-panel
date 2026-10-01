import { afterEach, describe, expect, it, vi } from 'vitest'
import { analyzeCandidate } from './client'

afterEach(() => vi.unstubAllGlobals())

function stream(lines: string) {
  return new Response(new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode(lines))
    controller.close()
  } }))
}

describe('candidate research transport', () => {
  it('requests the plan mode, carries cancellation and decodes trailing events', async () => {
    const fetchMock = vi.fn().mockResolvedValue(stream('{"type":"meta","as_of":"2026-09-30"}\n{"type":"delta","content":"观察条件"}\n{"type":"done"}'))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()
    const events = []
    for await (const event of analyzeCandidate('510300.SH', controller.signal)) events.push(event)
    const init = fetchMock.mock.calls[0][1]
    expect(JSON.parse(init.body)).toEqual({ symbol: '510300.SH', research_plan: true })
    expect(init.signal).toBe(controller.signal)
    expect(init.credentials).toBe('same-origin')
    expect(events.map(event => event.type)).toEqual(['meta', 'delta', 'done'])
  })

  it.each(['{"type":"delta"}garbage', '{"type":"delta","content":{}}', '{"type":"meta","as_of":{}}'])('rejects corrupt evidence instead of displaying it: %s', async line => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream(line)))
    const drain = async () => { for await (const event of analyzeCandidate('600000.SH')) void event }
    await expect(drain()).rejects.toThrow()
  })
})
