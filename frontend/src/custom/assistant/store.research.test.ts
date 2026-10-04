// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AssistantContext, AssistantEvent, ChatHistoryMessage } from './client'

const STORAGE_KEY = 'assistant.sessions.v1'

type ChatRequest = { messages: ChatHistoryMessage[]; context?: AssistantContext }
type Stream = (body: ChatRequest, signal?: AbortSignal) => AsyncGenerator<AssistantEvent>

interface StoredState {
  sessions: Array<{
    id: string
    title: string
    createdAt: number
    messages: Array<{ id: string; role: string; content?: string; ts: number }>
  }>
  activeId: string
}

function seedOldSession() {
  const session = {
    id: 'saved-session',
    title: '保存的旧对话',
    createdAt: 100,
    messages: [
      { id: 'old-user', role: 'user', content: '旧聊天中的私人内容', ts: 101 },
      { id: 'old-assistant', role: 'assistant', content: '旧回答', ts: 102, streaming: false },
    ],
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessions: [session], activeId: session.id }))
  return session
}

async function importStore(stream: Stream) {
  vi.doMock('./client', () => ({ assistantChatStream: stream }))
  return import('./store')
}

function storedState(): StoredState {
  return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') as StoredState
}

beforeEach(() => {
  vi.resetModules()
  vi.doUnmock('./client')
  localStorage.clear()
})

afterEach(() => {
  vi.doUnmock('./client')
  vi.resetModules()
  localStorage.clear()
})

describe('sendResearchMessage', () => {
  it('sends only the research prompt in a fresh session and preserves saved chat', async () => {
    const oldSession = seedOldSession()
    const requests: ChatRequest[] = []
    const stream: Stream = vi.fn(async function* (body: ChatRequest) {
      requests.push(body)
      yield { type: 'delta', content: '研究答复' } as const
    })
    const store = await importStore(stream)

    expect(store.sendResearchMessage('本次研究摘要')).toBe(true)
    await vi.waitFor(() => {
      expect(store.activeSession()?.messages.some(message => (
        message.role === 'assistant' && message.content === '研究答复' && !message.streaming
      ))).toBe(true)
    })

    expect(requests).toHaveLength(1)
    expect(requests[0].messages).toEqual([{ role: 'user', content: '本次研究摘要' }])

    const saved = storedState()
    expect(saved.activeId).not.toBe(oldSession.id)
    expect(saved.sessions).toHaveLength(2)
    expect(saved.sessions.find(session => session.id === oldSession.id)).toMatchObject(oldSession)
  })

  it('rejects research while another request is sending without aborting it', async () => {
    seedOldSession()
    const requests: ChatRequest[] = []
    const signals: Array<AbortSignal | undefined> = []
    let releaseStream: (() => void) | undefined
    let markStarted!: () => void
    const started = new Promise<void>(resolve => { markStarted = resolve })
    const stream: Stream = vi.fn(async function* (body: ChatRequest, signal?: AbortSignal) {
      requests.push(body)
      signals.push(signal)
      markStarted()
      await new Promise<void>(resolve => { releaseStream = resolve })
      yield { type: 'delta', content: '原请求完成' } as const
    })
    const store = await importStore(stream)

    expect(store.sendResearchMessage('第一次研究')).toBe(true)
    await started
    const activeId = store.activeSession()?.id

    expect(store.sendResearchMessage('不应发送的第二次研究')).toBe(false)
    expect(store.activeSession()?.id).toBe(activeId)
    expect(stream).toHaveBeenCalledTimes(1)
    expect(signals[0]?.aborted).toBe(false)

    try {
      releaseStream?.()
      await vi.waitFor(() => {
        expect(store.activeSession()?.messages.some(message => (
          message.role === 'assistant' && message.content === '原请求完成' && !message.streaming
        ))).toBe(true)
      })
    } finally {
      releaseStream?.()
    }

    expect(requests[0].messages).toEqual([{ role: 'user', content: '第一次研究' }])
    expect(store.activeSession()?.messages.some(message => (
      message.role === 'user' && message.content === '不应发送的第二次研究'
    ))).toBe(false)
  })
})
