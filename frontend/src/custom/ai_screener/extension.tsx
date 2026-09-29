import { Sparkles } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'
import { AiScreenerPage } from './AiScreenerPage'

const extension: FrontendExtension = {
  id: 'ai.screener',
  apiVersion: 1,
  routes: [{ id: 'ai-screener', path: '/ai-screener', component: AiScreenerPage }],
  navigation: [{ id: 'ai-screener', routeId: 'ai-screener', label: 'AI 选股', icon: Sparkles, order: 205 }],
}

export default extension
