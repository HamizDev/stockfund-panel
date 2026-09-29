/**
 * 基金中心前端扩展 — 完全解耦模块。
 *
 * 注册 /fund 路由 + 侧边栏"基金"导航, 不修改任何核心文件。
 * 删除本目录即整体卸载。apiVersion: 1。
 */
import { PiggyBank, Sparkles } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'
import { FundPage } from './FundPage'
import { AiFundScreenerPage } from './AiFundScreenerPage'

const extension: FrontendExtension = {
  id: 'fund.center',
  apiVersion: 1,
  routes: [
    { id: 'fund-center', path: '/fund', component: FundPage },
    { id: 'ai-fund-screener', path: '/ai-fund-screener', component: AiFundScreenerPage },
  ],
  navigation: [
    {
      id: 'fund-center',
      routeId: 'fund-center',
      label: '基金',
      icon: PiggyBank,
      order: 320,
    },
    {
      id: 'ai-fund-screener',
      routeId: 'ai-fund-screener',
      label: 'AI 选基',
      icon: Sparkles,
      order: 321,
    },
  ],
}

export default extension
