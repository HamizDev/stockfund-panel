// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useCandidateResearch, type CandidateResearch } from './useCandidateResearch'
import type { Candidate, CandidateAnalysisEvent } from './client'

const mocked = vi.hoisted(() => ({ analyze: vi.fn() }))
vi.mock('./client', () => ({ analyzeCandidate: mocked.analyze }))
const item = { symbol: '600000.SH' } as Candidate
let root: Root
let node: HTMLDivElement
let research: CandidateResearch
function Harness() { research = useCandidateResearch(); return null }

beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  sessionStorage.clear()
  mocked.analyze.mockReset()
  node = document.createElement('div')
  document.body.append(node)
  root = createRoot(node)
  await act(async () => { root.render(<Harness />) })
})
afterEach(async () => { await act(async () => root.unmount()); node.remove(); vi.unstubAllGlobals() })

describe('shared card and side-panel research', () => {
  it('stores a complete real stream under the requested target, not the selected card', async () => {
    mocked.analyze.mockImplementation(async function* () {
      yield { type: 'meta', as_of: '2026-09-30' }
      yield { type: 'delta', content: '条件分析' }
      yield { type: 'done' }
    })
    await act(async () => { await research.generate(item, 'stock') })
    expect(mocked.analyze.mock.calls[0][0]).toBe('600000.SH')
    expect(research.reports['stock:600000.SH']).toMatchObject({ content: '条件分析', complete: true, asOf: '2026-09-30' })
    expect(JSON.parse(sessionStorage.getItem('stockfund.candidate-research.v1') || '{}')['stock:600000.SH'].complete).toBe(true)
  })
  it('never retains truncated or failed analysis as complete', async () => {
    mocked.analyze.mockImplementation(async function* () { yield { type: 'delta', content: '未完成片段' } })
    await act(async () => { await research.generate(item, 'stock') })
    expect(research.drafts['stock:600000.SH']).toMatchObject({ content: '未完成片段', complete: false, error: '分析未完整返回，请重试' })
    expect(research.reports['stock:600000.SH']).toBeUndefined()
    expect(sessionStorage.getItem('stockfund.candidate-research.v1')).toBe('{}')
  })
  it('keeps the previous completed report when a reanalysis fails', async () => {
    mocked.analyze.mockImplementationOnce(async function* () { yield { type: 'delta', content: '上次完整报告' }; yield { type: 'done' } })
    await act(async () => { await research.generate(item, 'stock') })
    mocked.analyze.mockImplementationOnce(async function* () { yield { type: 'delta', content: '新片段' }; yield { type: 'error', message: '网络中断' } })
    await act(async () => { await research.generate(item, 'stock') })
    expect(research.reports['stock:600000.SH']).toMatchObject({ content: '上次完整报告', complete: true })
    expect(research.drafts['stock:600000.SH']).toMatchObject({ content: '新片段', error: '网络中断', complete: false })
    expect(JSON.parse(sessionStorage.getItem('stockfund.candidate-research.v1') || '{}')['stock:600000.SH'].content).toBe('上次完整报告')
  })
  it('permits only one in-flight model job and preserves its identity while changing targets', async () => {
    let release: () => void = () => {}
    const gate = new Promise<void>(resolve => { release = resolve })
    mocked.analyze.mockImplementation(async function* (): AsyncGenerator<CandidateAnalysisEvent> {
      await gate
      yield { type: 'delta', content: '第一只研究' }
      yield { type: 'done' }
    })
    let pending: Promise<void>
    await act(async () => { pending = research.generate(item, 'stock') })
    await act(async () => { await research.generate({ symbol: '510300' } as Candidate, 'etf') })
    expect(mocked.analyze).toHaveBeenCalledTimes(1)
    expect(research.runningKey).toBe('stock:600000.SH')
    await act(async () => { release(); await pending! })
    expect(research.reports['etf:510300']).toBeUndefined()
    expect(research.runningKey).toBeNull()
  })
})
