import { Sparkles } from 'lucide-react'
import { lazy } from 'react'
import type { FrontendExtension } from '@/extensions/types'

const AiScreenerPage = lazy(() => import('./AiScreenerPage').then(module => ({ default: module.AiScreenerPage })))
const AiScreenerRoute = () => <AiScreenerPage />

const extension: FrontendExtension = {
  id: 'ai.screener',
  apiVersion: 1,
  routes: [{ id: 'ai-screener', path: '/ai-screener', component: AiScreenerRoute }],
  navigation: [{ id: 'ai-screener', routeId: 'ai-screener', label: 'AI 选股', icon: Sparkles, order: 205 }],
}

export default extension
