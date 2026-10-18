import { Globe2 } from 'lucide-react'
import { lazy } from 'react'
import type { FrontendExtension } from '@/extensions/types'

const MarketResearchPage = lazy(() => import('./MarketResearchPage').then(module => ({ default: module.MarketResearchPage })))
const HongKongMarketRoute = () => <MarketResearchPage market="hk" />
const UsMarketRoute = () => <MarketResearchPage market="us" />

const extension: FrontendExtension = {
  id: 'global.markets',
  apiVersion: 1,
  routes: [
    { id: 'hong-kong-market-research', path: '/markets/hk', component: HongKongMarketRoute },
    { id: 'us-market-research', path: '/markets/us', component: UsMarketRoute },
  ],
  navigation: [
    { id: 'hong-kong-market-research', routeId: 'hong-kong-market-research', label: '港股研究', icon: Globe2, order: 115 },
    { id: 'us-market-research', routeId: 'us-market-research', label: '美股研究', icon: Globe2, order: 116 },
  ],
}

export default extension
