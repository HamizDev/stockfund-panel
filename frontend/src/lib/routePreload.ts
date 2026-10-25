type RouteLoader = () => Promise<unknown>
type RouteIntent = {
  cancelled: boolean
  ready: boolean
  timer?: ReturnType<typeof setTimeout>
}
type PendingRoute = {
  path: string
  intents: Set<RouteIntent>
  queued: boolean
}

const POINTER_DELAY_MS = 120
const MAX_CONCURRENT_IMPORTS = 2

// Keep these paths in sync with router.tsx's existing lazy imports. Importing a
// page module evaluates it but does not render the component or run its hooks.
const routeLoaders: Record<string, RouteLoader> = {
  '/': () => import('../pages/Dashboard').then(module => module.Dashboard),
  '/analysis/:menuId': () => import('../pages/AnalysisDetail').then(module => module.AnalysisDetail),
  '/concept-analysis': () => import('../pages/ConceptAnalysis').then(module => module.ConceptAnalysis),
  '/industry-analysis': () => import('../pages/IndustryAnalysis').then(module => module.IndustryAnalysis),
  '/stock-analysis': () => import('../pages/StockAnalysis').then(module => module.StockAnalysis),
  '/review': () => import('../pages/Review').then(module => module.Review),
  '/watchlist': () => import('../pages/Watchlist').then(module => module.Watchlist),
  '/screener': () => import('../pages/Screener').then(module => module.Screener),
  '/backtest': () => import('../pages/Backtest').then(module => module.Backtest),
  '/factors': () => import('../pages/Factors').then(module => module.Factors),
  '/financials': () => import('../pages/Financials').then(module => module.Financials),
  '/data': () => import('../pages/Data').then(module => module.Data),
  '/monitor': () => import('../pages/Monitor').then(module => module.Monitor),
  '/lots': () => import('../pages/Lots').then(module => module.Lots),
  '/paper': () => import('../pages/Paper').then(module => module.Paper),
  '/signals': () => import('../pages/Signals').then(module => module.Signals),
  '/limit-ladder': () => import('../pages/LimitUpLadder').then(module => module.LimitUpLadder),
  '/indices': () => import('../pages/Indices').then(module => module.Indices),
  '/regime': () => import('../pages/Regime').then(module => module.Regime),
  '/abnormal': () => import('../pages/AbnormalMoves').then(module => module.AbnormalMoves),
  '/branding': () => import('../pages/Branding').then(module => module.Branding),
  '/settings': () => import('../pages/Settings').then(module => module.Settings),
  '/dev': () => import('../pages/Dev').then(module => module.Dev),
}

// These router entries redirect to a lazy page rather than rendering their own
// module. Preload only that destination page when the alias itself gets intent.
const routeAliases: Record<string, string> = {
  '/overview': '/',
  '/analysis': '/settings',
  '/mining': '/factors',
  '/settings/keys': '/settings',
  '/settings/ai': '/settings',
  '/settings/queries': '/settings',
}

function resolveRouteKey(href: string): string | undefined {
  if (!href) return undefined

  try {
    const origin = typeof window === 'undefined' ? 'http://route-preload.invalid' : window.location.origin
    const url = new URL(href, typeof window === 'undefined' ? origin : window.location.href)
    if (url.origin !== origin) return undefined

    const path = url.pathname.length > 1 ? url.pathname.replace(/\/+$/, '') : url.pathname
    if (routeAliases[path]) return routeAliases[path]
    if (routeLoaders[path]) return path
    if (/^\/analysis\/[^/]+$/.test(path)) return '/analysis/:menuId'
    return undefined
  } catch {
    return undefined
  }
}

/**
 * Build a preloader around route loaders. Exported so scheduling behavior can
 * be tested with stubs without importing page modules.
 */
export function createRoutePreloader(
  loaders: Record<string, RouteLoader>,
  pointerDelayMs = POINTER_DELAY_MS,
) {
  const loaded = new Set<string>()
  const inFlight = new Map<string, Promise<unknown>>()
  const pendingByPath = new Map<string, PendingRoute>()
  const queue: PendingRoute[] = []
  let activeImports = 0

  const removeFromQueue = (pending: PendingRoute) => {
    if (!pending.queued) return
    const index = queue.indexOf(pending)
    if (index >= 0) queue.splice(index, 1)
    pending.queued = false
  }

  const pump = () => {
    while (activeImports < MAX_CONCURRENT_IMPORTS && queue.length > 0) {
      const pending = queue.shift()!
      pending.queued = false
      if (
        pendingByPath.get(pending.path) !== pending ||
        !Array.from(pending.intents).some(intent => !intent.cancelled && intent.ready)
      ) continue

      pendingByPath.delete(pending.path)
      for (const intent of pending.intents) {
        if (intent.timer !== undefined) clearTimeout(intent.timer)
      }
      pending.intents.clear()

      // Catch both rejected imports and synchronous loader errors. The handled
      // Promise is shared by concurrent intents; failures are evicted so a
      // later intent can retry, while the router keeps its own retryImport path.
      activeImports += 1
      const promise = Promise.resolve().then(loaders[pending.path])
      inFlight.set(pending.path, promise)
      void promise.then(
        () => {
          loaded.add(pending.path)
          inFlight.delete(pending.path)
          activeImports -= 1
          pump()
        },
        () => {
          inFlight.delete(pending.path)
          activeImports -= 1
          pump()
        },
      )
    }
  }

  const enqueue = (pending: PendingRoute) => {
    if (
      pendingByPath.get(pending.path) !== pending ||
      pending.queued ||
      !Array.from(pending.intents).some(intent => !intent.cancelled && intent.ready)
    ) return
    pending.queued = true
    queue.push(pending)
    pump()
  }

  return function scheduleRoutePreload(
    href: string,
    options: { immediate?: boolean } = {},
  ): () => void {
    const path = resolveRouteKey(href)
    if (!path || !loaders[path] || loaded.has(path) || inFlight.has(path)) return () => {}

    let pending = pendingByPath.get(path)
    if (!pending) {
      pending = { path, intents: new Set(), queued: false }
      pendingByPath.set(path, pending)
    }

    const intent: RouteIntent = { cancelled: false, ready: options.immediate === true }
    pending.intents.add(intent)
    if (intent.ready) {
      enqueue(pending)
    } else {
      intent.timer = setTimeout(() => {
        intent.timer = undefined
        if (intent.cancelled) return
        intent.ready = true
        enqueue(pending!)
      }, pointerDelayMs)
    }

    return () => {
      if (intent.cancelled) return
      intent.cancelled = true
      if (intent.timer !== undefined) {
        clearTimeout(intent.timer)
        intent.timer = undefined
      }
      pending!.intents.delete(intent)
      if (pendingByPath.get(path) !== pending) return

      if (pending!.intents.size === 0) {
        removeFromQueue(pending!)
        pendingByPath.delete(path)
      } else if (!Array.from(pending!.intents).some(other => !other.cancelled && other.ready)) {
        // An immediate request may have been cancelled while a pointer intent
        // for the same page is still inside its delay window.
        removeFromQueue(pending!)
      }
      pump()
    }
  }
}

const preloadRoute = createRoutePreloader(routeLoaders)

/** Preload one known lazy route after pointer intent, or immediately on focus. */
export function scheduleRoutePreload(
  href: string,
  options: { immediate?: boolean } = {},
): () => void {
  return preloadRoute(href, options)
}
