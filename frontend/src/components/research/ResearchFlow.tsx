import { Link } from 'react-router-dom'
import { FlaskConical, Layers, Sparkles } from 'lucide-react'
import { researchLink } from './researchContext'

export function ResearchFlow({ active, strategyId, assetType = 'stock', onCreate }: {
  active: 'strategy' | 'backtest' | 'paper'; strategyId?: string | null; assetType?: string; onCreate?: () => void
}) {
  const steps = [
    { id: 'strategy', title: '1 · 理解与生成策略', description: '先看买卖条件与数据要求', icon: Sparkles, href: researchLink('screener', strategyId, assetType) },
    { id: 'backtest', title: '2 · 历史回测', description: '检查费后收益、回撤与样本', icon: FlaskConical, href: researchLink('backtest', strategyId, assetType) },
    { id: 'paper', title: '3 · 独立虚拟仓', description: '默认暂停，启用后观察后续成交', icon: Layers, href: researchLink('paper', strategyId, assetType) },
  ]
  return <section aria-label="策略研究流程" className="rounded-card border border-border bg-surface/70 p-3">
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted">
      <span>从规则到验证 · AI 帮你理解，成交与统计由模拟引擎计算</span>
      {onCreate && <button type="button" onClick={onCreate} className="text-accent hover:underline">描述想法，让 AI 生成草案 →</button>}
    </div>
    <div className="grid gap-2 md:grid-cols-3">{steps.map(step => <Link key={step.id} to={step.href}
      aria-current={active === step.id ? 'step' : undefined}
      className={`min-w-0 rounded-btn border px-3 py-2 transition-colors ${active === step.id ? 'border-accent/30 bg-accent/10' : 'border-border hover:border-accent/40'}`}>
      <div className="flex items-center gap-2 text-xs font-medium"><step.icon className="h-3.5 w-3.5 shrink-0 text-accent" />{step.title}</div>
      <div className="mt-1 text-[11px] text-muted">{step.description}</div>
    </Link>)}</div>
  </section>
}
