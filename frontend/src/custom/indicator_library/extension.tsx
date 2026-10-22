import { lazy } from 'react'
import type { FrontendExtension } from '@/extensions/types'

const IndicatorLibraryPage = lazy(() => import('./IndicatorLibraryPanel').then(m => ({ default: m.IndicatorLibraryPage })))

const extension: FrontendExtension = {
  id: 'research.indicators',
  apiVersion: 1,
  routes: [{ id: 'indicator-library', path: '/indicator-library', component: IndicatorLibraryPage }],
}

export default extension
