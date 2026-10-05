// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StrategyBuilderDialog } from './StrategyBuilderDialog'
import { storage } from '@/lib/storage'

const apiMocks = vi.hoisted(() => ({
  strategyAiStatus: vi.fn(),
  strategyAiIterate: vi.fn(),
  strategyList: vi.fn(),
  strategySaveCodeV2: vi.fn(),
  strategyValidateCode: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  api: apiMocks,
  friendlyStreamError: (error: unknown) => String(error),
}))

vi.mock('@/components/Modal', () => ({
  Modal: ({ children }: { children: ReactNode }) => <div role="dialog">{children}</div>,
}))

vi.mock('@/components/Toast', () => ({ toast: vi.fn() }))

const DRAFT_ID = 'ai_iterated_fixture'
const OTHER_ID = 'ai_other_1'
// 合成指标，仅验证组件行为，非真实回测结果。
const ROUND = {
  round: 1,
  stats: { total_return: 0.18, max_drawdown: -0.07, sharpe: 1.36, win_rate: 0.58 },
  change_summary: '加入成交量确认条件',
}

function strategyCode(id: string) {
  return [
    'META = {',
    `    "id": "${id}",`,
    '    "name": "测试策略",',
    '    "description": "组件回归测试策略",',
    '}',
    '',
    'RULES = """',
    '收盘价站上短期均线',
    '"""',
    '',
    'def filter(df):',
    '    return df',
  ].join('\n')
}

const ITERATED_CODE = strategyCode(DRAFT_ID)
const ITERATE_RESULT = {
  draft_strategy_id: DRAFT_ID,
  rounds: [ROUND],
  final_code: ITERATED_CODE,
  final_meta: { id: DRAFT_ID, name: '测试策略' },
}

type DialogProps = {
  open: boolean
  mode?: 'create' | 'modify'
  onClose?: () => void
  onSavedId?: (id: string, researchOnly?: boolean) => void | Promise<void>
  existingStrategyIds?: ReadonlySet<string>
}

const EMPTY_IDS = new Set<string>()
const noop = () => {}
let root: Root | null = null
const container = document.createElement('div')
document.body.appendChild(container)

async function renderDialog(props: DialogProps) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  if (!root) root = createRoot(container)
  await act(async () => {
    root!.render(
      <StrategyBuilderDialog
        open={props.open}
        mode={props.mode ?? 'create'}
        onClose={props.onClose ?? noop}
        onSavedId={props.onSavedId}
        existingStrategyIds={props.existingStrategyIds ?? EMPTY_IDS}
      />,
    )
    await Promise.resolve()
    await Promise.resolve()
  })
}

async function settle() {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve()
  })
}

function setFieldValue(element: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype = element.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set
  if (!setter) throw new Error('Missing native value setter')
  act(() => {
    setter.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

function findButton(text: string): HTMLButtonElement {
  const button = [...container.querySelectorAll('button')]
    .find(candidate => candidate.textContent?.includes(text))
  if (!button) throw new Error(`Button containing ${JSON.stringify(text)} was not found`)
  return button
}

async function clickButton(text: string) {
  const button = findButton(text)
  await act(async () => {
    button.click()
    for (let i = 0; i < 5; i++) await Promise.resolve()
  })
}

async function fillIterationForm() {
  const name = container.querySelector('input[placeholder^="策略名称"]') as HTMLInputElement | null
  const rules = container.querySelector('textarea') as HTMLTextAreaElement | null
  if (!name || !rules) throw new Error('AI strategy form fields were not rendered')
  setFieldValue(name, '测试策略')
  setFieldValue(rules, '收盘价站上短期均线')
  await clickButton('AI 迭代（自动回测诊断优化）')
}

async function unmount() {
  if (root) {
    await act(async () => { root!.unmount() })
    root = null
  }
  container.innerHTML = ''
}

function legacyDraft(id: string, overrides: Record<string, unknown> = {}) {
  return {
    name: '旧版迭代草稿',
    description: '保留既有迭代代码',
    direction: 'long',
    rules: '收盘价站上短期均线',
    code: strategyCode(id),
    step: 2,
    strategyId: id,
    source: 'ai' as const,
    ...overrides,
  }
}

function listedResearchDraft(id: string) {
  return { id, source: 'ai', research_only: true }
}

function configureApiMocks() {
  vi.clearAllMocks()
  apiMocks.strategyAiStatus.mockResolvedValue({ configured: true, has_key: true, has_model: true })
  apiMocks.strategyAiIterate.mockResolvedValue(ITERATE_RESULT)
  apiMocks.strategyList.mockResolvedValue({ strategies: [] })
  apiMocks.strategyValidateCode.mockResolvedValue({ valid: true, matches_existing_research_draft: false })
  apiMocks.strategySaveCodeV2.mockImplementation(async (payload: any) => ({
    ok: true,
    strategy_id: payload.strategy_id,
    source: payload.target_source,
    path: '',
    meta: {},
    research_only: true,
  }))
}

beforeEach(() => {
  localStorage.clear()
  configureApiMocks()
})

afterEach(async () => {
  await unmount()
})

it('restores iteration evidence after unmount/remount and verifies before saving the existing draft', async () => {
  const onSavedId = vi.fn()
  apiMocks.strategyList.mockResolvedValue({ strategies: [listedResearchDraft(DRAFT_ID)] })
  await renderDialog({ open: true, onSavedId })
  await fillIterationForm()
  await clickButton('AI 迭代生成')

  expect(container.textContent).toContain(`迭代证据（共 1 轮，草稿已保存为 ${DRAFT_ID}）`)
  expect(container.textContent).toContain(ROUND.change_summary)
  expect((storage.strategyDraft.get(null) as any)?.iterateRounds).toEqual([ROUND])

  // 列表轮询即使出现该 ID，也不能重放旧草稿并清掉当前迭代结果。
  await renderDialog({ open: true, onSavedId, existingStrategyIds: new Set([DRAFT_ID]) })
  expect(container.textContent).toContain(ROUND.change_summary)

  await unmount()
  await renderDialog({ open: true, onSavedId })
  expect(container.textContent).toContain(`迭代证据（共 1 轮，草稿已保存为 ${DRAFT_ID}）`)
  expect(container.textContent).toContain(ROUND.change_summary)

  await clickButton('保存策略')
  await settle()

  expect(apiMocks.strategyList).toHaveBeenCalledWith(undefined, 'all', true)
  expect(apiMocks.strategySaveCodeV2).not.toHaveBeenCalled()
  expect(onSavedId).toHaveBeenCalledWith(DRAFT_ID, true)
})

it('persists an iteration that finishes while the same dialog instance is hidden', async () => {
  let resolveIterate!: (value: typeof ITERATE_RESULT) => void
  const pendingResult = new Promise<typeof ITERATE_RESULT>(resolve => { resolveIterate = resolve })
  apiMocks.strategyAiIterate.mockReturnValue(pendingResult)
  apiMocks.strategyList.mockResolvedValue({ strategies: [listedResearchDraft(DRAFT_ID)] })
  const onClose = vi.fn()
  const onSavedId = vi.fn()

  await renderDialog({ open: true, onClose, onSavedId })
  await fillIterationForm()
  await clickButton('AI 迭代生成')
  expect(apiMocks.strategyAiIterate).toHaveBeenCalledTimes(1)

  const closeButton = container.querySelector('button[aria-label="关闭"]') as HTMLButtonElement | null
  if (!closeButton) throw new Error('Close button was not rendered')
  await act(async () => { closeButton.click() })
  await renderDialog({ open: false, onClose, onSavedId })
  expect(onClose).toHaveBeenCalledTimes(1)

  await act(async () => {
    resolveIterate(ITERATE_RESULT)
    await pendingResult
    for (let i = 0; i < 5; i++) await Promise.resolve()
  })
  expect((storage.strategyDraft.get(null) as any)?.iterateDraftId).toBe(DRAFT_ID)
  expect((storage.strategyDraft.get(null) as any)?.iterateRounds).toEqual([ROUND])

  await renderDialog({ open: true, onClose, onSavedId })
  expect(container.textContent).toContain(ROUND.change_summary)
  expect(container.textContent).toContain(DRAFT_ID)

  await clickButton('保存策略')
  await settle()
  expect(apiMocks.strategyList).toHaveBeenCalledWith(undefined, 'all', true)
  expect(apiMocks.strategySaveCodeV2).not.toHaveBeenCalled()
  expect(onSavedId).toHaveBeenCalledWith(DRAFT_ID, true)
})

it('safely recovers an old-format AI research draft and forces an update when its saved-code baseline is absent', async () => {
  const draft = legacyDraft(DRAFT_ID)
  storage.strategyDraft.set(draft)
  apiMocks.strategyList.mockResolvedValue({ strategies: [listedResearchDraft(DRAFT_ID)] })
  await renderDialog({ open: true })

  await clickButton('保存策略')
  await settle()

  expect(apiMocks.strategyList).toHaveBeenCalledWith(undefined, 'all', true)
  expect(apiMocks.strategySaveCodeV2).toHaveBeenCalledTimes(1)
  expect(apiMocks.strategySaveCodeV2).toHaveBeenCalledWith({
    strategy_id: DRAFT_ID,
    code: draft.code,
    target_source: 'ai',
    mode: 'update',
    name: draft.name,
    description: draft.description,
  })
})

it.each([
  { quote: 'double', known: false }, { quote: 'single', known: false },
  { quote: 'double', known: true }, { quote: 'single', known: true },
])('recovers an iterator-assigned ID with $quote quotes (known iteration: $known) only after a server match', async ({ quote, known }) => {
  const rawCode = quote === 'single' ? strategyCode(OTHER_ID).replace(/"/g, "'") : strategyCode(OTHER_ID)
  const normalizedCode = strategyCode(DRAFT_ID)
  const onSavedId = vi.fn()
  storage.strategyDraft.set(legacyDraft(DRAFT_ID, {
    code: rawCode,
    ...(known ? { iterateDraftId: DRAFT_ID, iterateRounds: [ROUND], iterateSavedCode: rawCode } : {}),
  }))
  apiMocks.strategyList.mockResolvedValue({ strategies: [listedResearchDraft(DRAFT_ID)] })
  apiMocks.strategyValidateCode.mockResolvedValue({
    valid: true, code: normalizedCode, meta: { id: DRAFT_ID, research_only: true },
    matches_existing_research_draft: true,
  })
  await renderDialog({ open: true, onSavedId })
  await clickButton('保存策略')
  await settle()
  expect(apiMocks.strategyValidateCode).toHaveBeenCalledWith({
    code: rawCode, strategy_id: DRAFT_ID,
  })
  expect(apiMocks.strategySaveCodeV2).not.toHaveBeenCalled()
  expect(onSavedId).toHaveBeenCalledWith(DRAFT_ID, true)
})

it.each([
  {
    name: 'published AI strategy',
    id: DRAFT_ID,
    codeId: DRAFT_ID,
    listing: { id: DRAFT_ID, source: 'ai', research_only: false },
    checksListing: true,
  },
  {
    name: 'strategy from another source',
    id: DRAFT_ID,
    codeId: DRAFT_ID,
    listing: { id: DRAFT_ID, source: 'custom', research_only: true },
    checksListing: true,
  },
  {
    name: 'mismatched code ID',
    id: DRAFT_ID,
    codeId: OTHER_ID,
    listing: listedResearchDraft(DRAFT_ID),
    checksListing: true,
  },
])('does not auto-update a legacy draft when it is $name', async ({ id, codeId, listing, checksListing }) => {
  const draft = legacyDraft(id, { code: strategyCode(codeId) })
  storage.strategyDraft.set(draft)
  apiMocks.strategyList.mockResolvedValue({ strategies: [listing] })
  await renderDialog({ open: true })

  await clickButton('保存策略')
  await settle()

  if (checksListing) expect(apiMocks.strategyList).toHaveBeenCalledWith(undefined, 'all', true)
  else expect(apiMocks.strategyList).not.toHaveBeenCalled()
  if (codeId !== id) {
    expect(apiMocks.strategySaveCodeV2).not.toHaveBeenCalled()
    expect(container.textContent).toContain('草稿 ID 与代码不一致')
    return
  }
  expect(apiMocks.strategySaveCodeV2).toHaveBeenCalledTimes(1)
  expect(apiMocks.strategySaveCodeV2.mock.calls[0][0]).toMatchObject({
    strategy_id: DRAFT_ID,
    target_source: 'ai',
    mode: 'create',
  })
})

it('refuses to write a known iteration if its code ID mismatches or the draft was published', async () => {
  const invalidDrafts = [
    {
      codeId: OTHER_ID,
      listed: listedResearchDraft(DRAFT_ID),
      expectedError: '草稿 ID 与代码不一致',
      shouldCheckList: true,
    },
    {
      codeId: DRAFT_ID,
      listed: { id: DRAFT_ID, source: 'ai', research_only: false },
      expectedError: '原 AI 草稿已删除或发布',
      shouldCheckList: true,
    },
  ]

  for (const invalid of invalidDrafts) {
    await unmount()
    localStorage.clear()
    configureApiMocks()
    storage.strategyDraft.set(legacyDraft(DRAFT_ID, {
      code: strategyCode(invalid.codeId),
      iterateDraftId: DRAFT_ID,
      iterateRounds: [ROUND],
      iterateSavedCode: strategyCode(DRAFT_ID),
    }) as any)
    apiMocks.strategyList.mockResolvedValue({ strategies: [invalid.listed] })
    await renderDialog({ open: true })

    await clickButton('保存策略')
    await settle()

    expect(container.textContent).toContain(invalid.expectedError)
    expect(apiMocks.strategySaveCodeV2).not.toHaveBeenCalled()
    if (invalid.shouldCheckList) expect(apiMocks.strategyList).toHaveBeenCalledWith(undefined, 'all', true)
    else expect(apiMocks.strategyList).not.toHaveBeenCalled()
  }
})

it('preserves the ordinary AI create, custom create, and modify save paths', async () => {
  const scenarios = [
    { mode: 'create' as const, store: 'ai' as const, id: 'ai_regular_1', source: 'ai' as const, button: '保存策略', expectedMode: 'create' as const },
    { mode: 'create' as const, store: 'custom' as const, id: 'custom_regular_1', source: 'custom' as const, button: '保存自定义策略', expectedMode: 'create' as const },
    { mode: 'modify' as const, store: 'modify' as const, id: 'ai_existing_1', source: 'ai' as const, button: '保存修改', expectedMode: 'update' as const },
  ]

  for (const scenario of scenarios) {
    await unmount()
    localStorage.clear()
    configureApiMocks()
    const draft = legacyDraft(scenario.id, { source: scenario.source, code: strategyCode(scenario.id) })
    if (scenario.store === 'modify') storage.strategyModify.set(draft)
    else storage.strategyDraft.set(draft)
    await renderDialog({ open: true, mode: scenario.mode })

    await clickButton(scenario.button)
    await settle()

    expect(apiMocks.strategySaveCodeV2).toHaveBeenCalledTimes(1)
    expect(apiMocks.strategySaveCodeV2.mock.calls[0][0]).toMatchObject({
      strategy_id: scenario.id,
      code: draft.code,
      target_source: scenario.source,
      mode: scenario.expectedMode,
    })
    if (scenario.store !== 'ai') expect(apiMocks.strategyList).not.toHaveBeenCalled()
  }
})
