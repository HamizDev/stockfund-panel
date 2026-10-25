import { Coins, Landmark } from 'lucide-react'
import { lazy } from 'react'
import type { FrontendExtension } from '@/extensions/types'

const CryptoPaperPage = lazy(() => import('./CryptoPaperPage').then(module => ({ default: module.CryptoPaperPage })))
const CryptoPaperRoute = () => <CryptoPaperPage />
const OfficialDemoPage = lazy(() => import('./OfficialDemoPage').then(module => ({ default: module.OfficialDemoPage })))
const OfficialDemoRoute = () => <OfficialDemoPage />

const extension: FrontendExtension = {
  id: 'crypto.paper',
  apiVersion: 1,
  routes: [
    { id: 'crypto-paper', path: '/crypto-paper', component: CryptoPaperRoute },
    { id: 'crypto-demo', path: '/crypto-demo', component: OfficialDemoRoute },
  ],
  navigation: [
    { id: 'crypto-paper', routeId: 'crypto-paper', label: '数字资产模拟', icon: Coins, order: 333 },
    { id: 'crypto-demo', routeId: 'crypto-demo', label: '交易所模拟', icon: Landmark, order: 334 },
  ],
}

export default extension
