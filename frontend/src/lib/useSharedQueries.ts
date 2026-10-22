/**
 * 共享 query hooks — 消除多页面重复的 useQuery 调用。
 *
 * 实时数据走 SSE invalidation，无需前端轮询。
 * 只有管线进度等非 SSE 数据才用 refetchInterval。
 */
import { useQuery, type QueryClient } from '@tanstack/react-query'
import { api } from './api'
import { QK } from './queryKeys'

// ===== 全局共享 =====

/** 能力检测 — Layout / Data / Keys 共用 */
export function useCapabilities() {
  return useQuery({
    queryKey: QK.capabilities,
    queryFn: api.capabilities,
    staleTime: 30_000,
  })
}

/** 能力路由矩阵 — 设置页与各页能力门控共用。

 * 区别于 useCapabilities 的 TickFlow 套餐视角: 矩阵按「生效源当前能否提供」
 * 判定 (usable), 路由到可用插件时同样可用。路由偏好 / 档位探测 / 插件装卸
 * 变化时由设置页失效, 各页共享同一缓存自动刷新。
 */
export function useCapabilityMatrix() {
  return useQuery({
    queryKey: QK.capabilityMatrix,
    queryFn: api.capabilityMatrix,
    staleTime: 30_000,
  })
}

/** 设置状态 — Layout / Data / Keys 共用 */
export function useSettings() {
  return useQuery({
    queryKey: QK.settings,
    queryFn: api.settings,
    staleTime: 30_000,
  })
}

/** 用户偏好 — Layout / Data / Intraday 共用 */
export function usePreferences() {
  return useQuery({
    queryKey: QK.preferences,
    queryFn: api.preferences,
    staleTime: 30_000,
  })
}

/** 行情状态 — SSE quotes_updated 自动刷新。

 * poll=true 时启用 60s 状态轮询兜底, 用于在交易时段边界
 * (11:30午休 / 13:00开盘 / 15:00收盘) 同步 quote status。
 * SSE 会在行情更新时即时刷新, 轮询负责没有 SSE 的休盘边界。
 * 只应在全局唯一挂载处 (Layout) 传 poll=true, 避免多页面重复轮询;
 * 其他调用方共享同一 queryKey 缓存, 无需自行轮询。
 */
export function useQuoteStatus(opts?: { enabled?: boolean; poll?: boolean }) {
  return useQuery({
    queryKey: QK.quoteStatus,
    queryFn: api.quoteStatus,
    enabled: opts?.enabled ?? true,
    refetchInterval: opts?.poll ? 60_000 : false,
  })
}

/** 行情间隔 — Layout / Data 共用 */
export function useQuoteInterval() {
  return useQuery({
    queryKey: QK.quoteInterval,
    queryFn: api.quoteInterval,
  })
}

/** 版本号 — Layout 专用 */
export function useVersion() {
  return useQuery({
    queryKey: QK.version,
    queryFn: api.version,
    staleTime: Infinity,
  })
}

/** 指数行情 — Layout / Indices 共用，行情 SSE 按 index-quotes 前缀刷新。 */
export function useIndexQuotes() {
  return useQuery({
    queryKey: QK.indexQuotes,
    queryFn: () => api.indexQuotes(),
    staleTime: 30_000,
  })
}

/** 管线任务列表 — Layout / Data / Dashboard 共用；仅 poll owner 定时检查。 */
export function usePipelineJobs(opts?: { enabled?: boolean; poll?: boolean }) {
  return useQuery({
    queryKey: QK.pipelineJobs,
    queryFn: () => api.pipelineJobs(15),
    enabled: opts?.enabled ?? true,
    staleTime: 15_000,
    refetchInterval: opts?.poll
      ? query => query.state.data?.active_id ? 2_000 : 60_000
      : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  })
}

/** 任务启动成功后立即唤醒共享状态，并刷新包含新任务的历史列表。 */
export function notifyPipelineJobStarted(queryClient: QueryClient, jobId: string) {
  queryClient.setQueryData<Awaited<ReturnType<typeof api.pipelineJobs>>>(QK.pipelineJobs, old => ({
    active_id: jobId,
    jobs: old?.jobs ?? [],
  }))
  void queryClient.invalidateQueries({ queryKey: QK.pipelineJobs })
}

/** 数据状态 — Data / Screener 共用 */
export function useDataStatus(opts?: {
  staleTime?: number
  refetchInterval?: number | false | ((query: any) => number | false | undefined)
}) {
  return useQuery({
    queryKey: QK.dataStatus,
    queryFn: api.dataStatus,
    staleTime: opts?.staleTime,
    refetchInterval: opts?.refetchInterval,
  })
}
