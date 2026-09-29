import { lazy } from 'react'
import { createBrowserRouter, Navigate, useSearchParams } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Onboarding } from './pages/Onboarding'
import { Auth } from './pages/Auth'
import { useSettings } from './lib/useSharedQueries'
import { Logo } from './components/Logo'
import { ExtensionBoundary } from './extensions/ExtensionBoundary'
import {
  finalizeFrontendExtensions,
  getFrontendExtensionLoadErrors,
  getFrontendExtensionRoutes,
} from './extensions/registry'

// 代码分割: 页面全部 lazy 加载, 避免首屏打包所有页面 (ECharts / lightweight-charts /
// framer-motion 等重库) → 大幅减小首屏 bundle。命名导出用 .then 映射为 default。
// Layout / Onboarding / Auth 为应用外壳与入口, 保持同步加载。

// 动态 import 失败自动重试: 免费隧道偶发闪断时, 路由 chunk 可能正好在断线瞬间加载而失败。
// 重试 3 次 (间隔 800ms/1600ms/3200ms, 共约 5.6s)。重试仍失败: 很可能是前端刚发了新版、
// 当前 index.html 引用的旧 chunk 已被删除 → 硬刷新一次拿新 index.html。
// sessionStorage 标记防刷新死循环; App 成功挂载后会清除标记 (见 main.tsx), 每次 fresh load 都有一次自动刷新机会。
function retryImport<T>(fn: () => Promise<T>, retries = 3, delayMs = 800): Promise<T> {
  return fn().catch((err: unknown) => {
    if (retries > 0) {
      return new Promise<T>((resolve, reject) => {
        window.setTimeout(() => {
          retryImport(fn, retries - 1, delayMs * 2).then(resolve, reject)
        }, delayMs)
      })
    }
    const msg = err instanceof Error ? err.message : String(err)
    if (
      /failed to fetch dynamically imported module|loading chunk/i.test(msg) &&
      !sessionStorage.getItem('tsp-chunk-reloaded')
    ) {
      sessionStorage.setItem('tsp-chunk-reloaded', '1')
      window.location.reload()
      return new Promise<T>(() => {}) // 永不 resolve, 等待刷新
    }
    throw err
  })
}

// 路由 chunk 加载失败时的友好错误页: 显示重试按钮 (清标记后硬刷新)。
export function ChunkErrorFallback() {
  const retry = () => {
    sessionStorage.removeItem('tsp-chunk-reloaded')
    window.location.reload()
  }
  return (
    <div style={{ padding: 48, textAlign: 'center', color: '#e5e7eb' }}>
      <div style={{ fontSize: 18, marginBottom: 12 }}>页面模块加载失败</div>
      <div style={{ fontSize: 14, opacity: 0.7, marginBottom: 24 }}>
        网络闪断导致页面文件没加载下来，点重试即可恢复
      </div>
      <button
        onClick={retry}
        style={{
          padding: '10px 28px', fontSize: 15, borderRadius: 8, border: 'none',
          background: '#6366f1', color: '#fff', cursor: 'pointer',
        }}
      >
        重试
      </button>
    </div>
  )
}
const Watchlist = lazy(() => retryImport(() => import('./pages/Watchlist').then(m => ({ default: m.Watchlist }))))
const Screener = lazy(() => retryImport(() => import('./pages/Screener').then(m => ({ default: m.Screener }))))
const Backtest = lazy(() => retryImport(() => import('./pages/Backtest').then(m => ({ default: m.Backtest }))))
const Factors = lazy(() => retryImport(() => import('./pages/Factors').then(m => ({ default: m.Factors }))))
const Financials = lazy(() => retryImport(() => import('./pages/Financials').then(m => ({ default: m.Financials }))))
const Data = lazy(() => retryImport(() => import('./pages/Data').then(m => ({ default: m.Data }))))
const Monitor = lazy(() => retryImport(() => import('./pages/Monitor').then(m => ({ default: m.Monitor }))))
const Lots = lazy(() => retryImport(() => import('./pages/Lots').then(m => ({ default: m.Lots }))))
const Paper = lazy(() => retryImport(() => import('./pages/Paper').then(m => ({ default: m.Paper }))))
const Dashboard = lazy(() => retryImport(() => import('./pages/Dashboard').then(m => ({ default: m.Dashboard }))))
const AnalysisDetail = lazy(() => retryImport(() => import('./pages/AnalysisDetail').then(m => ({ default: m.AnalysisDetail }))))
const ConceptAnalysis = lazy(() => retryImport(() => import('./pages/ConceptAnalysis').then(m => ({ default: m.ConceptAnalysis }))))
const IndustryAnalysis = lazy(() => retryImport(() => import('./pages/IndustryAnalysis').then(m => ({ default: m.IndustryAnalysis }))))
const StockAnalysis = lazy(() => retryImport(() => import('./pages/StockAnalysis').then(m => ({ default: m.StockAnalysis }))))
const Signals = lazy(() => retryImport(() => import('./pages/Signals').then(m => ({ default: m.Signals }))))
const Review = lazy(() => retryImport(() => import('./pages/Review').then(m => ({ default: m.Review }))))
const LimitUpLadder = lazy(() => retryImport(() => import('./pages/LimitUpLadder').then(m => ({ default: m.LimitUpLadder }))))
const Indices = lazy(() => retryImport(() => import('./pages/Indices').then(m => ({ default: m.Indices }))))
const Branding = lazy(() => retryImport(() => import('./pages/Branding').then(m => ({ default: m.Branding }))))
const Settings = lazy(() => retryImport(() => import('./pages/Settings').then(m => ({ default: m.Settings }))))
const Regime = lazy(() => retryImport(() => import('./pages/Regime').then(m => ({ default: m.Regime }))))
const AbnormalMoves = lazy(() => retryImport(() => import('./pages/AbnormalMoves').then(m => ({ default: m.AbnormalMoves }))))
const Dev = lazy(() => retryImport(() => import('./pages/Dev').then(m => ({ default: m.Dev }))))

const CORE_ROUTE_PATHS = new Set([
  '/',
  '/onboarding',
  '/login',
  '/overview',
  '/analysis',
  '/analysis/:menuId',
  '/concept-analysis',
  '/industry-analysis',
  '/stock-analysis',
  '/review',
  '/watchlist',
  '/screener',
  '/backtest',
  '/factors',
  '/mining',
  '/financials',
  '/data',
  '/monitor',
  '/limit-ladder',
  '/indices',
  '/regime',
  '/abnormal',
  '/branding',
  '/settings',
  '/dev',
  '/settings/keys',
  '/settings/ai',
  '/settings/queries',
])

finalizeFrontendExtensions(CORE_ROUTE_PATHS)
const frontendExtensionRoutes = getFrontendExtensionRoutes()
const frontendExtensionErrors = getFrontendExtensionLoadErrors()
if (frontendExtensionErrors.length > 0) {
  console.error('部分前端扩展加载失败', frontendExtensionErrors)
}

// 旧链接兼容: 挖掘已并入因子页 (/factors?tab=mining), 保留 run/candidate 等参数重定向
function MiningRedirect() {
  const [searchParams] = useSearchParams()
  const search = searchParams.toString()
  return <Navigate to={`/factors?tab=mining${search ? `&${search}` : ''}`} replace />
}

// 首次使用守卫 —— 未完成向导则重定向到 /onboarding
// 只挂在根路由上;/onboarding 本身不被守卫,避免循环重定向。
// settings 由 Layout 预取,守卫判定不产生额外请求。
function OnboardingGuard({ children }: { children: React.ReactNode }) {
  const settings = useSettings()

  // 仅首次加载(本地无缓存)时显示占位。
  // 后台重取 (isFetching) 时本地已有上一份缓存可用, 直接放行, 避免切页时整屏 logo 闪烁。
  // 防误重定向已由 Onboarding/AI 等处 invalidate 前的 setQueryData 同步缓存兜底。
  if (settings.isLoading) {
    return (
      <div className="min-h-screen bg-base grid place-items-center">
        <div className="flex flex-col items-center gap-3 text-muted">
          <Logo size={28} className="text-foreground" />
          <div className="text-xs">加载中…</div>
        </div>
      </div>
    )
  }

  // 查询出错或字段缺失时不拦截 —— 宁可放行,也不把用户卡在空白页
  if (settings.data && settings.data.onboarding_completed === false) {
    return <Navigate to="/onboarding" replace />
  }

  return <>{children}</>
}

export const router = createBrowserRouter([
  { path: '/onboarding', element: <Onboarding />, errorElement: <ChunkErrorFallback /> },
  { path: '/login', element: <Auth />, errorElement: <ChunkErrorFallback /> },
  {
    path: '/',
    element: (
      <OnboardingGuard>
        <Layout />
      </OnboardingGuard>
    ),
    errorElement: <ChunkErrorFallback />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'overview', element: <Navigate to="/" replace /> },
      { path: 'analysis', element: <Navigate to="/settings?tab=ext-pages" replace /> },
      { path: 'analysis/:menuId', element: <AnalysisDetail /> },
      { path: 'concept-analysis', element: <ConceptAnalysis /> },
      { path: 'industry-analysis', element: <IndustryAnalysis /> },
      { path: 'stock-analysis', element: <StockAnalysis /> },
      { path: 'review', element: <Review /> },
      { path: 'watchlist', element: <Watchlist /> },
      { path: 'screener', element: <Screener /> },
      { path: 'backtest', element: <Backtest /> },
      { path: 'factors', element: <Factors /> },
      { path: 'mining', element: <MiningRedirect /> },
      { path: 'financials', element: <Financials /> },
      { path: 'data', element: <Data /> },
      { path: 'monitor', element: <Monitor /> },
      { path: 'lots', element: <Lots /> },
      { path: 'paper', element: <Paper /> },
      { path: 'signals', element: <Signals /> },
      { path: 'limit-ladder', element: <LimitUpLadder /> },
      { path: 'indices', element: <Indices /> },
    { path: 'regime', element: <Regime /> },
      { path: 'abnormal', element: <AbnormalMoves /> },
      { path: 'branding', element: <Branding /> },
      { path: 'settings', element: <Settings /> },
      // 隐藏路由：开发者工具（不暴露在菜单，仅供调试）
      { path: 'dev', element: <Dev /> },
      // 旧路由兼容重定向
      { path: 'settings/keys', element: <Navigate to="/settings?tab=data-sources" replace /> },
      { path: 'settings/ai', element: <Navigate to="/settings?tab=ai" replace /> },
      { path: 'settings/queries', element: <Navigate to="/settings?tab=queries" replace /> },
      ...frontendExtensionRoutes.map(route => {
        const ExtensionPage = route.component
        return {
          path: route.path.slice(1),
          element: (
            <ExtensionBoundary extensionId={route.extensionId}>
              <ExtensionPage />
            </ExtensionBoundary>
          ),
        }
      }),
    ],
  },
])
