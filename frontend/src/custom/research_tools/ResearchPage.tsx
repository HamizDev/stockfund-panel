import { useSearchParams } from 'react-router-dom'
import { PageHeader } from '@/components/PageHeader'
import { usePageState } from '@/lib/usePageState'
import { PortfolioPanel } from './PortfolioPanel'
import { LibraryPanel } from './LibraryPanel'
import { IndustrySignalsPanel, SnapshotPanel } from './SnapshotPanel'
import { InstrumentPicker } from './InstrumentPicker'

const TABS = { portfolio:'持仓体检', industry:'产业指标', hk:'港股补充', events:'事件日历', library:'本地资料库' } as const
type View = {tab: keyof typeof TABS; market: 'cn'|'hk'|'us'; symbol:string; name:string; hkSymbol:string; hkName:string}
const initial: View = {tab:'portfolio',market:'cn',symbol:'600519.SH',name:'贵州茅台',hkSymbol:'00700.HK',hkName:'腾讯控股'}
function valid(value: unknown): value is View { const r=value as View | null;return !!r && r.tab in TABS && ['cn','hk','us'].includes(r.market) && [r.symbol,r.name,r.hkSymbol,r.hkName].every(v=>typeof v==='string' && v.length<100) }
export default function ResearchPage() {
  const [search,setSearch]=useSearchParams()
  const [view,setView]=usePageState<View>('researchTools',initial,valid)
  const raw=search.get('tab') ?? view.tab
  const tab=raw in TABS ? raw as keyof typeof TABS : 'portfolio'
  const {market,symbol,hkSymbol}=view
  return <><PageHeader title="研究工具" subtitle="数字可追溯 · 组合风险 · 产业与事件 · 本地资料" /><div className="min-w-0 space-y-4 p-3 md:p-5">
    <nav className="flex flex-wrap gap-2" aria-label="研究工具栏目">{Object.entries(TABS).map(([key,label])=><button key={key} onClick={()=>{setView(previous=>({...previous,tab:key as keyof typeof TABS}));setSearch({tab:key})}} className={`rounded-btn border px-3 py-2 text-xs ${tab===key?'border-accent/40 bg-accent/10 text-accent':'border-border text-muted hover:bg-elevated'}`}>{label}</button>)}</nav>
    {tab==='portfolio' && <PortfolioPanel initialAccount={search.get('account') ?? undefined} />}
    {tab==='industry' && <IndustrySignalsPanel />}
    {tab==='library' && <LibraryPanel />}
    {tab==='hk' && <><InstrumentPicker market="hk" onSelect={(next,name)=>setView(previous=>({...previous,hkSymbol:next,hkName:name}))} /><p className="text-sm font-medium">{view.hkName}<span className="ml-2 text-xs text-muted">{hkSymbol}</span></p><SnapshotPanel dataset="southbound_flow" market="hk" title="南向资金每日流向" /><SnapshotPanel dataset="southbound_holding" market="hk" symbol={hkSymbol} title="个股南向持股" /><SnapshotPanel dataset="dividends" market="hk" symbol={hkSymbol} title="港股派息原始方案" /><SnapshotPanel dataset="ah_premium" market="hk" symbol={hkSymbol} title="AH对应与溢价" /></>}
    {tab==='events' && <><select aria-label="事件市场" value={market} onChange={e=>{const m=e.target.value as View['market'];setView(previous=>({...previous,market:m,symbol:m==='cn'?'600519.SH':m==='hk'?'00700.HK':'AAPL',name:m==='cn'?'贵州茅台':m==='hk'?'腾讯控股':'苹果'}))}} className="rounded-btn border border-border bg-surface px-3 py-2 text-xs"><option value="cn">A股</option><option value="hk">港股</option><option value="us">美股</option></select><InstrumentPicker market={market} onSelect={(next,name)=>setView(previous=>({...previous,symbol:next,name}))} /><p className="text-sm font-medium">{view.name}<span className="ml-2 text-xs text-muted">{symbol}</span></p><SnapshotPanel dataset="events" market={market} symbol={symbol} title="财报与派息日历" /></>}
  </div></>
}
