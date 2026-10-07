import { Newspaper } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'
import { NewsPage } from './NewsPage'

const extension: FrontendExtension = {
  id: 'market.news', apiVersion: 1,
  routes: [{ id: 'market-news', path: '/news', component: NewsPage }],
  navigation: [{ id: 'market-news', routeId: 'market-news', label: '快讯', icon: Newspaper, order: 300 }],
}
export default extension
