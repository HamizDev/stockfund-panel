// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { SnapshotPanel, IndustrySignalsPanel } from './SnapshotPanel'

vi.mock('@/lib/api',()=>({api:{researchSnapshot:vi.fn(),researchRefresh:vi.fn()}}))
vi.mock('@/components/research/ResearchAIButton',()=>({ResearchAIButton:()=>null}))
const saved={status:'ready',stale:false,error:null,cached_at:123,snapshot:{dataset:'gpu_rent',status:'ok',items:[{name:'B200',value:3,date:'2026-10-24',unit:'USD/卡时',series:[{date:'2026-10-24',value:3}]}],unit:{price:'USD/卡时'}}} as const
let host: HTMLDivElement, root: Root, client: QueryClient
beforeEach(()=>{Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});vi.clearAllMocks();vi.mocked(api.researchSnapshot).mockResolvedValue(saved as never);host=document.createElement('div');document.body.append(host);root=createRoot(host);client=new QueryClient({defaultOptions:{queries:{retry:false}}})})
afterEach(async()=>{await act(async()=>root.unmount());client.clear();host.remove()})
async function show(node: ReactNode) {await act(async()=>{root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)});await act(async()=>{await new Promise(resolve=>setTimeout(resolve,30))})}
it('restores a cached snapshot without starting network refresh and supports structured units',async()=>{
  await show(<SnapshotPanel dataset="gpu_rent" title="GPU" />)
  expect(host.textContent).toContain('B200')
  expect(api.researchRefresh).not.toHaveBeenCalled()
  expect(host.textContent).toContain('USD/卡时')
})
it('retains previous values when explicit refresh fails',async()=>{
  vi.mocked(api.researchRefresh).mockRejectedValue(new Error('更新暂不可用'))
  await show(<SnapshotPanel dataset="gpu_rent" title="GPU" />)
  await act(async()=>{host.querySelector<HTMLButtonElement>('button')!.click();await new Promise(resolve=>setTimeout(resolve,30))})
  expect(host.querySelector('[role="alert"]')).toBeTruthy()
  expect(host.textContent).toContain('B200')
  expect(api.researchRefresh).toHaveBeenCalledTimes(1)
})
it('does not mount industry queries while the section is collapsed',async()=>{
  await show(<IndustrySignalsPanel />)
  expect(api.researchSnapshot).not.toHaveBeenCalled()
})
