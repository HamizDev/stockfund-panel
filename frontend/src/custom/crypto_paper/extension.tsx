import { Coins } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'
import { CryptoPaperPage } from './CryptoPaperPage'

const extension: FrontendExtension = {
  id: 'crypto.paper',
  apiVersion: 1,
  routes: [{ id: 'crypto-paper', path: '/crypto-paper', component: CryptoPaperPage }],
  navigation: [{ id: 'crypto-paper', routeId: 'crypto-paper', label: '数字资产模拟', icon: Coins, order: 333 }],
}

export default extension
