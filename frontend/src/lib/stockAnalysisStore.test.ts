import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({ stockAnalysisReportsList: vi.fn() }))
vi.mock('./api', () => ({ api: { stockAnalysisReportsList: calls.stockAnalysisReportsList } }))

import { findLatestHistoryReport, findTodayReport, loadHistory, resetHistory } from './stockAnalysisStore'

beforeEach(() => {
  calls.stockAnalysisReportsList.mockReset()
  resetHistory()
})

afterEach(() => resetHistory())

it('shares one in-flight report request between page load and an early analysis action', async () => {
  let resolve!: (value: { reports: { id: string; symbol: string; name: string; focus: string; content: string; created_at: string }[] }) => void
  calls.stockAnalysisReportsList.mockReturnValueOnce(new Promise(resolveRequest => { resolve = resolveRequest }))

  const mountLoad = loadHistory()
  const actionLoad = findTodayReport('600000.SH')

  resolve({ reports: [{
    id: 'today', symbol: '600000.SH', name: '浦发银行', focus: '', content: '',
    created_at: new Date().toISOString(),
  }] })
  await Promise.all([mountLoad, expect(actionLoad).resolves.toMatchObject({ id: 'today' })])
  expect(calls.stockAnalysisReportsList).toHaveBeenCalledTimes(1)
})

it('allows a retry after the report request fails', async () => {
  calls.stockAnalysisReportsList
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce({ reports: [{
      id: 'retried', symbol: '600000.SH', name: '浦发银行', focus: '', content: '',
      created_at: new Date().toISOString(),
    }] })

  await loadHistory()
  await loadHistory()

  expect(calls.stockAnalysisReportsList).toHaveBeenCalledTimes(2)
  await expect(findLatestHistoryReport('600000.SH')).resolves.toMatchObject({ id: 'retried' })
})

it('does not let a request started before reset overwrite the new history', async () => {
  let resolveOld!: (value: { reports: { id: string; symbol: string; name: string; focus: string; content: string; created_at: string }[] }) => void
  let resolveNew!: (value: { reports: { id: string; symbol: string; name: string; focus: string; content: string; created_at: string }[] }) => void
  calls.stockAnalysisReportsList
    .mockReturnValueOnce(new Promise(resolveRequest => { resolveOld = resolveRequest }))
    .mockReturnValueOnce(new Promise(resolveRequest => { resolveNew = resolveRequest }))

  const oldLoad = loadHistory()
  await Promise.resolve()
  resetHistory()
  const newLoad = loadHistory()
  await Promise.resolve()
  expect(calls.stockAnalysisReportsList).toHaveBeenCalledTimes(2)

  resolveNew({ reports: [{
    id: 'new', symbol: '600000.SH', name: '浦发银行', focus: '', content: '',
    created_at: new Date().toISOString(),
  }] })
  await newLoad
  resolveOld({ reports: [{
    id: 'old', symbol: '600000.SH', name: '旧结果', focus: '', content: '',
    created_at: new Date().toISOString(),
  }] })
  await oldLoad

  await expect(findLatestHistoryReport('600000.SH')).resolves.toMatchObject({ id: 'new' })
})
