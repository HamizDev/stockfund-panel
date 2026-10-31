import { lazy } from 'react'
import { TrendingUp } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { FrontendExtension, FrontendSlotContextMap } from '@/extensions/types'
import { kronosSearchParams } from './state'

const KronosPage = lazy(() => import('./KronosPage').then(module => ({ default: module.KronosPage })))
const KronosRoute = () => <KronosPage />

type AnySlotContext = FrontendSlotContextMap[keyof FrontendSlotContextMap]

function StockPreviewEntry(props: AnySlotContext) {
  const { symbol, name, view } = props as FrontendSlotContextMap['stock-preview.footer']
  const params = kronosSearchParams(
    { symbol, name: name ?? symbol },
    view === 'intraday' ? '1m' : 'daily',
  )
  return (
    <div className="border-t border-border/40 px-4 py-2">
      <Link
        to={`/kronos?${params.toString()}`}
        className="inline-flex h-7 items-center rounded-btn border border-accent/30 bg-accent/10 px-2.5 text-[11px] font-medium text-accent transition-colors hover:bg-accent/20"
      >
        K线预测
      </Link>
    </div>
  )
}

const extension: FrontendExtension = {
  id: 'kronos.prediction',
  apiVersion: 1,
  routes: [{ id: 'kronos-prediction', path: '/kronos', component: KronosRoute }],
  navigation: [
    {
      id: 'kronos-prediction',
      routeId: 'kronos-prediction',
      label: 'K线预测',
      icon: TrendingUp,
      order: 185,
    },
  ],
  slots: [
    {
      name: 'stock-preview.footer',
      id: 'kronos-prediction-entry',
      order: 185,
      component: StockPreviewEntry,
    },
  ],
}

export default extension
