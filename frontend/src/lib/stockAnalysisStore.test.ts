import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({
  stockAnalysisReportsList: vi.fn(),
  stockAnalyzeStream: vi.fn(),
  stockAnalysisReportSave: vi.fn(),
  stockAnalysisReportDelete: vi.fn(),
}))
vi.mock('./api', () => ({
  api: {
    stockAnalysisReportsList: calls.stockAnalysisReportsList,
    stockAnalyzeStream: calls.stockAnalyzeStream,
    stockAnalysisReportSave: calls.stockAnalysisReportSave,
    stockAnalysisReportDelete: calls.stockAnalysisReportDelete,
  },
  friendlyStreamError: (message: string) => message,
}))

import { findLatestHistoryReport, findTodayReport, loadHistory, resetHistory, startAnalysis } from './stockAnalysisStore'

beforeEach(() => {
  calls.stockAnalysisReportsList.mockReset()
  calls.stockAnalyzeStream.mockReset()
  calls.stockAnalysisReportSave.mockReset()
  calls.stockAnalysisReportDelete.mockReset()
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

it('preserves the shared snapshot cutoff from stream meta through report persistence', async () => {
  const report = {
    id: 'fresh', symbol: '600000.SH', name: '浦发银行', focus: '', content: '分析',
    as_of: '2026-10-22', daily_as_of: '2026-10-21',
    requested_at: '2026-10-22T11:30:42+08:00',
    intraday_as_of: '2026-10-22T11:30:00+08:00', is_provisional: true,
    price_basis: 'shared_snapshot', created_at: new Date().toISOString(),
  }
  calls.stockAnalyzeStream.mockImplementation(async function* () {
    yield {
      type: 'meta', summary: '实时分析', close: 12.3,
      as_of: report.as_of, daily_as_of: report.daily_as_of,
      requested_at: report.requested_at,
      intraday_as_of: report.intraday_as_of, is_provisional: true,
      price_basis: report.price_basis,
    }
    yield { type: 'delta', content: '分析' }
    yield { type: 'done' }
  })
  calls.stockAnalysisReportSave.mockResolvedValue({ ok: true, report })

  await startAnalysis('600000.SH', '浦发银行')
  await vi.waitFor(() => expect(calls.stockAnalysisReportSave).toHaveBeenCalledTimes(1))

  expect(calls.stockAnalysisReportSave).toHaveBeenCalledWith(expect.objectContaining({
    as_of: '2026-10-22',
    requested_at: '2026-10-22T11:30:42+08:00',
    daily_as_of: '2026-10-21',
    intraday_as_of: '2026-10-22T11:30:00+08:00',
    is_provisional: true,
    price_basis: 'shared_snapshot',
  }))
  expect(await findLatestHistoryReport('600000.SH')).toMatchObject({
    as_of: '2026-10-22',
    requested_at: '2026-10-22T11:30:42+08:00',
    daily_as_of: '2026-10-21',
    intraday_as_of: '2026-10-22T11:30:00+08:00',
    is_provisional: true,
    price_basis: 'shared_snapshot',
  })
})

it('loads legacy history without inventing a quote cutoff from created_at', async () => {
  calls.stockAnalysisReportsList.mockResolvedValue({ reports: [{
    id: 'legacy', symbol: '600000.SH', name: '浦发银行', focus: '', content: '旧报告',
    created_at: '2026-10-22T15:00:00+08:00',
  }] })

  await loadHistory()
  const report = await findLatestHistoryReport('600000.SH')

  expect(report).toMatchObject({ id: 'legacy', created_at: '2026-10-22T15:00:00+08:00' })
  expect(report).not.toHaveProperty('as_of')
  expect(report).not.toHaveProperty('intraday_as_of')
})
