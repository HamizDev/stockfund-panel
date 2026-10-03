// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { FundRankItem, FundResearch } from './client'
import {
  FundCandidateCard,
  formatFundCandidateReturn,
  fundAnalysisStatusLabel,
  fundAnalysisSummaryPresentation,
  fundCandidateBasis,
  fundCandidateDataGap,
  fundCandidateMetrics,
  summarizeFundAnalysisContent,
} from './FundCandidateCard'

const candidate: FundRankItem = {
  code: '012345',
  name: '测试基金',
  fund_type: '混合型',
  share_class: 'A',
  nav: 1.2345,
  nav_date: '2026-09-30',
  purchase_fee_text: '0.15%',
  growth_1w: 0.5,
  growth_1m: 2.35,
  growth_3m: -1.4,
  growth_6m: 3.1,
  growth_1y: 9.2,
  growth_2y: 12.5,
  growth_3y: null,
}

const research: FundResearch = {
  thscode: '012345.OF',
  source: 'eastmoney',
  retrieved_at_ms: 1_790_000_000_000,
  horizon: '1y',
  nav_date: '2026-09-30',
  fees: {
    status: 'partial',
    management_pct: 0.5,
    custody_pct: 0.1,
    sales_service_pct: null,
    subscription_rules: [],
    redemption_rules: [],
    source_url: '',
    as_of: null,
    note: '',
  },
  risk: {
    status: 'ok',
    basis: 'unit_nav',
    max_drawdown_pct: 12.3,
    start_date: '2025-10-01',
    end_date: '2026-09-30',
    observations: 248,
    note: '按单位净值序列观测',
    source_url: '',
  },
  holdings: {
    status: 'ok',
    report_date: '2026-06-30',
    publication_date: null,
    items: [{ thscode: '600000.SH', name: '浦发银行', hold_ratio: 3.2, asset_type: 'stock' }],
    coverage_weight_pct: 63.5,
    report_note: '',
    source_url: '',
    related_reports: [],
  },
  missing_fields: [],
  warnings: [],
}

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('FundCandidateCard display formatting', () => {
  it('formats historical returns with signed percentages and explicit missing values', () => {
    expect(formatFundCandidateReturn(2.345)).toBe('+2.35%')
    expect(formatFundCandidateReturn(-1.2)).toBe('-1.20%')
    expect(formatFundCandidateReturn(0)).toBe('0.00%')
    expect(formatFundCandidateReturn(null)).toBe('未提供')
  })

  it('extracts a bounded plain-text excerpt from actual streamed Markdown content', () => {
    expect(summarizeFundAnalysisContent('## 结论\n后续可关注 **久期变化**。')).toBe('结论 后续可关注 久期变化。')
    const bounded = summarizeFundAnalysisContent('模型结论。'.repeat(40), 20)
    expect(bounded.length).toBe(21)
    expect(bounded.endsWith('…')).toBe(true)
    expect(summarizeFundAnalysisContent('```text\n不应展示代码\n```')).toBe('')
  })

  it('marks summaries complete only for done status with actual streamed content', () => {
    const generatedAt = 1_790_000_000_000
    expect(fundAnalysisSummaryPresentation('done', '模型实际结论。', generatedAt)).toEqual({
      label: `AI 分析摘要 · 完成于 ${new Date(generatedAt).toLocaleString()}：`,
      text: '模型实际结论。',
      complete: true,
    })

    for (const status of ['partial', 'cancelled', 'error'] as const) {
      const presentation = fundAnalysisSummaryPresentation(status, '模型返回的未完成片段。', generatedAt, '流已停止')
      expect(presentation.label).toBe('AI 分析摘要（未完成）：')
      expect(presentation.text).toBe('模型返回的未完成片段。')
      expect(presentation.complete).toBe(false)
    }

    const noDeltaContent = fundAnalysisSummaryPresentation('done', '', generatedAt)
    expect(noDeltaContent.label).toBe('AI 分析摘要（未生成）：')
    expect(noDeltaContent.text).toBe('尚未生成 AI 摘要。')
    expect(noDeltaContent.complete).toBe(false)

    const markdownOnly = fundAnalysisSummaryPresentation('done', '```text\n隐藏代码块\n```', generatedAt)
    expect(markdownOnly.complete).toBe(true)
    expect(markdownOnly.text).toContain('分析内容已返回')
  })

  it('shows only values returned by the candidate and public research fields', () => {
    const metrics = fundCandidateMetrics({ ...candidate, research }, '1m')
    expect(metrics).toHaveLength(6)
    expect(metrics.map(({ value }) => value)).toEqual([
      '+2.35%',
      '+9.20%',
      '12.30%',
      '248 个',
      '管理 0.50% · 托管 0.10% · 销售服务 未提供',
      '2026-06-30',
    ])
  })

  it('distinguishes unavailable research from missing returned ranking values', () => {
    const metrics = fundCandidateMetrics(candidate, '3y')
    expect(metrics[0].value).toBe('未提供')
    expect(metrics[2].value).toBe('待获取')
    expect(metrics[3].value).toBe('待获取')
    expect(metrics[4].value).toBe('待获取')
    expect(metrics[5].value).toBe('待获取')
    expect(fundCandidateDataGap()).toContain('研究资料待获取')
  })

  it('shows factual category, share class and return as candidate basis without a score', () => {
    const basis = fundCandidateBasis(candidate, 'all', '1m')
    expect(basis).toContain('混合型公开榜单')
    expect(basis).toContain('A 类份额')
    expect(basis).toContain('近 1 月收益 +2.35%')
    expect(basis).not.toContain('评分')
  })

  it('maps analysis lifecycle and research gaps to concise labels', () => {
    expect(fundAnalysisStatusLabel()).toBe('未分析')
    expect(fundAnalysisStatusLabel('cancelled')).toBe('已停止（当前未分析）')
    expect(fundAnalysisStatusLabel('loading')).toBe('进行中')
    expect(fundAnalysisStatusLabel('partial')).toBe('未完成')
    expect(fundAnalysisStatusLabel('done')).toBe('完成')
    expect(fundAnalysisStatusLabel('error')).toBe('失败')
    expect(fundCandidateDataGap({ ...research, warnings: ['公开来源暂不可用'] })).toBe('公开来源暂不可用；缺少或不完整：费率部分可用')
    expect(fundCandidateDataGap({ ...research, missing_fields: ['max_drawdown_pct', 'holdings'] })).toBe('缺少或不完整：观测回撤、定期报告持仓、费率部分可用')
    expect(fundCandidateDataGap({ ...research, warnings: ['资料缺口：费率'] })).not.toContain('资料缺口：资料缺口')
  })

  it('labels meta as research summary and partial streamed content as incomplete', async () => {
    await act(async () => root.render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><FundCandidateCard
      item={candidate}
      selected={false}
      resultFundType="all"
      horizon="1y"
      analysisStatus="cancelled"
      researchSummary="净值日期来自公开资料。"
      analysisContent="## 模型观点\n实际分析可关注 **回撤变化**。"
      analysisError="用户已停止接收"
      modelConfigured
      modelProvider="实际服务"
      onSelect={() => undefined}
      onAnalyze={() => undefined}
      onViewAnalysis={() => undefined}
    /></MemoryRouter>))

    const article = host.querySelector('article')
    const selection = article?.querySelector<HTMLButtonElement>('button[aria-pressed="false"]')
    expect(selection?.textContent).toContain('选择候选')
    expect(article?.querySelector('button')?.getAttribute('aria-pressed')).toBe('false')
    expect(article?.textContent).toContain('AI 已停止（当前未分析）')
    expect(article?.textContent).toContain('AI 服务 实际服务')
    expect(article?.textContent).toContain('资料摘要：净值日期来自公开资料。')
    expect(article?.textContent).toContain('AI 分析摘要（未完成）：')
    expect(article?.textContent).toContain('模型观点')
    expect(article?.textContent).toContain('实际分析可关注 回撤变化。')
    expect(article?.textContent).not.toContain('AI 分析摘要：净值日期来自公开资料。')
    expect(article?.querySelector('a[href^="/fund?"]')).not.toBeNull()
    expect(article?.textContent).toContain('复制代码')
    expect(Array.from(article?.querySelectorAll('button, a') ?? []).every((control) => !control.querySelector('button, a'))).toBe(true)
  })

  it('marks an analysis complete only when done content and completion time exist', async () => {
    const generatedAt = 1_790_000_000_000
    await act(async () => root.render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><FundCandidateCard
      item={candidate}
      selected
      resultFundType="all"
      horizon="1y"
      analysisStatus="done"
      researchSummary="来自资料元数据的摘要。"
      analysisContent="## 实际结论\n模型输出内容。"
      analysisGeneratedAt={generatedAt}
      modelConfigured
      onSelect={() => undefined}
      onAnalyze={() => undefined}
      onViewAnalysis={() => undefined}
    /></MemoryRouter>))

    const articleText = host.querySelector('article')?.textContent ?? ''
    expect(articleText).toContain('AI 完成')
    expect(articleText).toContain(`AI 分析摘要 · 完成于 ${new Date(generatedAt).toLocaleString()}：`)
    expect(articleText).toContain('资料摘要：来自资料元数据的摘要。')
    expect(articleText).toContain('实际结论')
    expect(articleText).toContain('模型输出内容。')
    expect(articleText).toContain('查看分析')
  })
})
