import { useEffect, useRef, useState } from 'react'
import { analyzeCandidate, type Candidate } from './client'

export type CandidateReport = { content: string; asOf: string | null; generatedAt: number; error: string; complete: boolean }
const STORAGE_KEY = 'stockfund.candidate-research.v1'
const MAX_REPORTS = 24

export function readCandidateReports(raw: string | null): Record<string, CandidateReport> {
  try {
    const parsed: unknown = JSON.parse(raw || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    return Object.fromEntries(Object.entries(parsed).filter(([, value]) => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) return false
      const r = value as Partial<CandidateReport>
      return typeof r.content === 'string' && r.content.length <= 30000 && !!r.content.trim()
        && r.complete === true && r.error === '' && typeof r.generatedAt === 'number' && Number.isFinite(r.generatedAt)
        && (r.asOf === null || typeof r.asOf === 'string')
    }).sort((a, b) => b[1].generatedAt - a[1].generatedAt).slice(0, MAX_REPORTS))
  } catch { return {} }
}

export function researchKey(assetType: 'stock' | 'etf', symbol: string) { return `${assetType}:${symbol}` }

export function reportExcerpt(content: string, limit = 140): string {
  return content.replace(/```[\s\S]*?```/g, '').replace(/[#*_>`|]/g, '').replace(/\s+/g, ' ').trim().slice(0, limit)
}

export function useCandidateResearch() {
  const [reports, setReports] = useState<Record<string, CandidateReport>>(() => {
    try { return readCandidateReports(sessionStorage.getItem(STORAGE_KEY)) } catch { return {} }
  })
  const [runningKey, setRunningKey] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, CandidateReport>>({})
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])

  async function generate(candidate: Candidate, assetType: 'stock' | 'etf') {
    if (controller.current) return
    const key = researchKey(assetType, candidate.symbol)
    const abort = new AbortController()
    controller.current = abort
    setRunningKey(key)
    let content = ''
    let asOf: string | null = null
    let done = false
    const update = (error = '') => setDrafts(prev => ({ ...prev, [key]: { content, asOf, generatedAt: Date.now(), error, complete: false } }))
    update()
    try {
      for await (const event of analyzeCandidate(candidate.symbol, abort.signal)) {
        if (event.type === 'meta') asOf = event.as_of ?? null
        if (event.type === 'delta') {
          content += event.content || ''
          if (content.length > 30000) throw new Error('分析内容过长，停止接收；请缩小范围')
          update()
        }
        if (event.type === 'error') throw new Error(event.message || '分析失败')
        if (event.type === 'done') done = true
      }
      if (abort.signal.aborted) throw new Error('分析已停止')
      if (!done || !content.trim()) throw new Error('分析未完整返回，请重试')
      setReports(prev => Object.fromEntries(Object.entries({ ...prev, [key]: { content, asOf, generatedAt: Date.now(), error: '', complete: true } })
        .sort((a, b) => b[1].generatedAt - a[1].generatedAt).slice(0, MAX_REPORTS)))
      setDrafts(prev => { const next = { ...prev }; delete next[key]; return next })
    } catch (error) {
      update(abort.signal.aborted ? '已停止接收，未完成内容仅供核对；模型任务可能稍后退出。' : error instanceof Error ? error.message : '分析失败')
    } finally {
      controller.current = null
      setRunningKey(null)
    }
  }

  useEffect(() => {
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(Object.entries(reports).filter(([, r]) => r.complete)))) }
    catch { /* Reports remain available in this page when storage is full. */ }
  }, [reports])

  return { reports, drafts, runningKey, generate, cancel: () => controller.current?.abort() }
}

export type CandidateResearch = ReturnType<typeof useCandidateResearch>
