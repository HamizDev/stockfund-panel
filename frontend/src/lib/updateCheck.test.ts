// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// useVersion 仅被 useUpdateCheck hook 使用; 打断 react-query 导入链, 单测只测命令式 API
vi.mock('@/lib/useSharedQueries', () => ({
  useVersion: () => ({ data: undefined }),
}))

import { checkForUpdate, compareVersion, getUpdateState } from './updateCheck'

const CACHE_KEY = 'stockfund_panel_update_check_cache_v1'
const REPO = 'HamizDev/stockfund-panel'
const RELEASES_URL = `https://github.com/${REPO}/releases`
const DAY = 24 * 60 * 60 * 1000

function seedCache(blob: Record<string, unknown>) {
  localStorage.setItem(CACHE_KEY, JSON.stringify({ repo: REPO, noRelease: false, ...blob }))
}

function readCacheRaw(): string | null {
  return localStorage.getItem(CACHE_KEY)
}

/** 模拟 GitHub Releases API 响应 (tag_name 可空以触发 latest.json 兜底) */
function mockFetchReleases(
  tagName: string,
  htmlUrl = `${RELEASES_URL}/tag/v${tagName.replace(/^v/, '')}`,
) {
  return vi.fn(async (url: unknown) => {
    const u = String(url)
    if (u.includes('api.github.com')) {
      return { ok: true, status: 200, json: async () => ({ tag_name: tagName, html_url: htmlUrl }) }
    }
    if (u.includes('latest.json')) {
      return { ok: true, status: 200, json: async () => ({
        tag: tagName,
        notes_url: `${RELEASES_URL}/tag/v${tagName.replace(/^v/, '')}`,
      }) }
    }
    return { ok: false, status: 500, json: async () => ({}) }
  })
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('compareVersion', () => {
  it('按语义版本比较大小', () => {
    expect(compareVersion('0.3.2', '0.3.3')).toBe(-1)
    expect(compareVersion('0.4.0', '0.3.9')).toBe(1)
    expect(compareVersion('1.0.0', '1.0')).toBe(0)
  })

  it('容忍 v 前缀与非法输入 (按 0.0.0 处理)', () => {
    expect(compareVersion('0.3.2', 'v0.3.3')).toBe(-1)
    expect(compareVersion('v1.2.3', '1.2.3')).toBe(0)
    expect(compareVersion('', '0.0.1')).toBe(-1)
  })
})

describe('checkForUpdate', () => {
  it('同版本 24h 内命中缓存, 不发网络请求', async () => {
    seedCache({
      current: '0.0.1', latest: '0.0.2', url: `${RELEASES_URL}/tag/v0.0.2`,
      found: true, noRelease: false, checkedAt: Date.now() - 1000,
    })
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('0.0.1')

    expect(fetchMock).not.toHaveBeenCalled()
    expect(getUpdateState().status).toBe('found')
    expect(getUpdateState().info?.latest).toBe('0.0.2')
    expect(getUpdateState().info?.url).toBe(`${RELEASES_URL}/tag/v0.0.2`)
  })

  it('缓存过期后重新请求并回写缓存', async () => {
    seedCache({
      current: '0.0.1', latest: '0.0.2', url: `${RELEASES_URL}/tag/v0.0.2`,
      found: true, noRelease: false, checkedAt: Date.now() - DAY - 1000,
    })
    vi.stubGlobal('fetch', mockFetchReleases('0.0.3'))

    await checkForUpdate('0.0.1')

    expect(getUpdateState().status).toBe('found')
    expect(getUpdateState().info?.latest).toBe('0.0.3')
    const cached = JSON.parse(readCacheRaw() ?? '{}')
    expect(cached).toMatchObject({
      repo: REPO, current: '0.0.1', latest: '0.0.3', found: true, noRelease: false,
    })
  })

  it('当前版本已是最新时状态为 latest', async () => {
    vi.stubGlobal('fetch', mockFetchReleases('v0.0.1'))

    await checkForUpdate('0.0.1')

    expect(getUpdateState().status).toBe('latest')
  })

  it('版本变化后缓存失效, 重新请求', async () => {
    seedCache({
      current: '0.0.0', latest: '0.0.2', url: `${RELEASES_URL}/tag/v0.0.2`,
      found: true, noRelease: false, checkedAt: Date.now(),
    })
    const fetchMock = mockFetchReleases('0.0.1')
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('0.0.1') // 本地版本已升级, 与缓存的 current 不一致

    expect(fetchMock).toHaveBeenCalled()
    expect(getUpdateState().status).toBe('latest')
  })

  it('force 绕过新鲜缓存立即刷新', async () => {
    seedCache({
      current: '0.0.1', latest: null, url: RELEASES_URL,
      found: false, noRelease: true, checkedAt: Date.now(),
    })
    const fetchMock = mockFetchReleases('0.0.2')
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('0.0.1', { force: true })

    expect(fetchMock).toHaveBeenCalled()
    expect(getUpdateState().status).toBe('found')
    expect(getUpdateState().info?.latest).toBe('0.0.2')
    expect(JSON.parse(readCacheRaw() ?? '{}')).toMatchObject({
      repo: REPO, current: '0.0.1', latest: '0.0.2', found: true, noRelease: false,
    })
  })

  it('最新 Release API 返回 404 时标记 unreleased 并缓存, 不请求 latest.json', async () => {
    const fetchMock = vi.fn(async (_url: unknown) => ({ ok: false, status: 404, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('0.0.1')

    expect(getUpdateState().status).toBe('unreleased')
    expect(getUpdateState().info?.latest ?? '').toBe('')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain(`api.github.com/repos/${REPO}/releases/latest`)
    expect(String(fetchMock.mock.calls[0][0])).not.toContain('latest.json')
    expect(JSON.parse(readCacheRaw() ?? '{}')).toMatchObject({
      repo: REPO, current: '0.0.1', noRelease: true,
    })
  })

  it('API 失败且没有可用缓存时状态 error、不落缓存 (下次启动重试)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) })))

    await checkForUpdate('0.0.1')

    expect(getUpdateState().status).toBe('error')
    expect(readCacheRaw()).toBeNull()
  })

  it.each([403, 429])('GitHub 返回 %s 限流时停止查询、不缓存且可重试', async (status) => {
    const fetchMock = vi.fn(async (_url: unknown) => ({
      ok: false, status, headers: new Headers({ 'x-ratelimit-remaining': '0' }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('0.0.1')

    expect(getUpdateState().status).toBe('limited')
    expect(getUpdateState().info).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(readCacheRaw()).toBeNull()

    vi.stubGlobal('fetch', mockFetchReleases('0.0.2'))
    await checkForUpdate('0.0.1', { force: true })
    expect(getUpdateState().status).toBe('found')
  })

  it('强制刷新网络失败时清除先前展示的 info', async () => {
    seedCache({
      current: '0.0.1', latest: '0.0.2', url: `${RELEASES_URL}/tag/v0.0.2`,
      found: true, noRelease: false, checkedAt: Date.now(),
    })
    vi.stubGlobal('fetch', vi.fn())
    await checkForUpdate('0.0.1')
    expect(getUpdateState().info?.latest).toBe('0.0.2')

    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) })))

    await checkForUpdate('0.0.1', { force: true })

    expect(getUpdateState().status).toBe('error')
    expect(getUpdateState().info).toBeNull()
  })

  it('其他仓库的缓存不命中', async () => {
    seedCache({
      repo: 'some-owner/other-project', current: '0.0.1', latest: '9.9.9',
      url: 'https://github.com/some-owner/other-project/releases/tag/v9.9.9',
      found: true, noRelease: false, checkedAt: Date.now(),
    })
    const fetchMock = mockFetchReleases('0.0.2')
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('0.0.1')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(getUpdateState().info?.latest).toBe('0.0.2')
    expect(JSON.parse(readCacheRaw() ?? '{}').repo).toBe(REPO)
  })

  it('API 响应里的外仓库下载链接回退到本仓库 Releases 页', async () => {
    const fetchMock = mockFetchReleases(
      '0.0.2',
      'https://github.com/some-owner/other-project/releases/tag/v0.0.2',
    )
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('0.0.1')

    expect(getUpdateState().status).toBe('found')
    expect(getUpdateState().info?.url).toBe(RELEASES_URL)
    expect(JSON.parse(readCacheRaw() ?? '{}').url).toBe(RELEASES_URL)
  })

  it('缓存含外仓库下载链接时拒绝缓存、重新查询并覆盖', async () => {
    seedCache({
      current: '0.0.1', latest: '0.0.2',
      url: 'https://github.com/some-owner/other-project/releases/tag/v0.0.2',
      found: true, noRelease: false, checkedAt: Date.now(),
    })
    const fetchMock = mockFetchReleases('0.0.3')
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('0.0.1')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(getUpdateState().info?.latest).toBe('0.0.3')
    expect(getUpdateState().info?.url).toBe(`${RELEASES_URL}/tag/v0.0.3`)
    expect(JSON.parse(readCacheRaw() ?? '{}')).toMatchObject({
      repo: REPO, latest: '0.0.3', url: `${RELEASES_URL}/tag/v0.0.3`,
    })
  })

  it('GitHub API 字段为空时回退清单并过滤外仓库链接', async () => {
    const fetchMock = vi.fn(async (url: unknown) => {
      const u = String(url)
      if (u.includes('api.github.com')) {
        return { ok: true, status: 200, json: async () => ({}) } // tag_name 缺失
      }
      if (u.includes('latest.json')) {
        return { ok: true, status: 200, json: async () => ({
          tag: '1.0.0', notes_url: 'https://github.com/some-owner/other-project/releases/tag/v1.0.0',
        }) }
      }
      return { ok: false, status: 500, json: async () => ({}) }
    })
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('0.3.2')

    expect(getUpdateState().status).toBe('found')
    expect(getUpdateState().info?.latest).toBe('1.0.0')
    expect(getUpdateState().info?.url).toBe(RELEASES_URL)
    expect(JSON.parse(readCacheRaw() ?? '{}').url).toBe(RELEASES_URL)
  })

  it('版本未知时空操作 (不请求不落缓存)', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('')

    expect(fetchMock).not.toHaveBeenCalled()
    expect(getUpdateState().status).not.toBe('checking')
  })
})
