import { Loader2, RefreshCw } from 'lucide-react'
import { PageHeader } from './PageHeader'

export function PageLoadingState({ title = '正在打开页面' }: { title?: string }) {
  return (
    <section aria-busy="true" aria-label={title} className="min-w-0">
      <PageHeader title={title} />
      <div className="space-y-4 px-4 py-5 sm:px-5">
        <p role="status" className="flex items-center gap-2 text-xs text-secondary">
          <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin text-accent" />
          请稍候，页面准备好后会自动显示。
        </p>
        <div aria-hidden="true" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[0, 1, 2, 3].map(key => <div key={key} className="h-20 animate-pulse rounded-card border border-border bg-surface" />)}
        </div>
        <div aria-hidden="true" className="h-56 animate-pulse rounded-card border border-border bg-surface" />
      </div>
    </section>
  )
}

export function PageStartupError() {
  return (
    <section className="mx-auto max-w-lg px-6 py-20 text-center">
      <h1 className="text-lg font-semibold text-foreground">看板未能完成启动</h1>
      <p role="alert" className="mt-3 text-sm leading-6 text-secondary">页面文件可能尚未加载完整，请刷新后重试。</p>
      <button type="button" onClick={() => window.location.reload()} className="mt-5 inline-flex min-h-10 items-center gap-2 rounded-btn border border-accent/40 bg-accent/10 px-4 text-sm text-accent hover:bg-accent/20">
        <RefreshCw aria-hidden="true" className="h-4 w-4" />刷新页面
      </button>
    </section>
  )
}
