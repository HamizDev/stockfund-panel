import { lazy } from 'react'
import { BookOpenCheck } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'
const Page=lazy(()=>import('./ResearchPage'))
const extension: FrontendExtension={id:'research.tools',apiVersion:1,
  routes:[{id:'research-tools',path:'/research-tools',component:Page}],
  navigation:[{id:'research-tools',routeId:'research-tools',label:'研究工具',icon:BookOpenCheck,order:180}]}
export default extension
