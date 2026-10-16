import { Newspaper } from 'lucide-react'
import { lazy } from 'react'
import type { FrontendExtension } from '@/extensions/types'

const NewsPage = lazy(() => import('./NewsPage').then(module => ({ default: module.NewsPage })))
const NewsRoute = () => <NewsPage />

const extension: FrontendExtension = {
  id: 'market.news', apiVersion: 1,
  routes: [{ id: 'market-news', path: '/news', component: NewsRoute }],
  navigation: [{ id: 'market-news', routeId: 'market-news', label: '快讯', icon: Newspaper, order: 300 }],
}
export default extension
