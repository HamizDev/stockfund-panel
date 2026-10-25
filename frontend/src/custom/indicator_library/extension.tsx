import { lazy } from 'react'
import { BookOpenCheck } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'

const IndicatorLibraryPage = lazy(() => import('./IndicatorLibraryPanel').then(m => ({ default: m.IndicatorLibraryPage })))

const extension: FrontendExtension = {
  id: 'research.indicators',
  apiVersion: 1,
  routes: [{ id: 'indicator-library', path: '/indicator-library', component: IndicatorLibraryPage }],
  navigation: [{ id: 'indicator-library', routeId: 'indicator-library', label: '指标研究', icon: BookOpenCheck, order: 181 }],
}

export default extension
