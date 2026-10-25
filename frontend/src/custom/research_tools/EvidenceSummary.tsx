import type { ReportEvidence } from './types'

export function EvidenceSummary({ evidence }: { evidence?: ReportEvidence | null }) {
  if (!evidence) return null
  const { verification: v, ledger } = evidence
  return <details className="rounded-card border border-border bg-surface p-3 text-xs" aria-label="报告数字核对">
    <summary className="cursor-pointer text-accent">数字核对：{v.bound}/{v.checked} 项与逐项引用对应{v.issues.length > 0 ? ` · ${v.issues.length} 项需复核` : ''}</summary>
    <p className="mt-2 text-muted">{v.limits} 仓位假设与真实持仓分开核对。</p>
    {v.unknown_references.length > 0 && <p className="mt-2 text-warning">存在未知引用：{v.unknown_references.join('、')}</p>}
    <ul className="my-2 space-y-1 text-warning">{v.issues.slice(0,12).map((r,i) => <li key={i}>第{r.line}行 · {r.number}：{r.reason}</li>)}</ul>
    <div className="max-h-64 overflow-auto"><table className="w-full text-left"><thead><tr className="text-muted"><th>引用</th><th>字段／计算</th><th>数值</th><th>资料日期</th></tr></thead><tbody>
      {[...ledger.facts,...ledger.calculations].map(r => <tr key={r.id} className="border-t border-border"><td className="py-1 font-mono text-[10px]">{r.id}</td><td className="max-w-60 break-all pr-3">{r.field}{r.inputs && <span className="block text-muted">{r.inputs.formula}</span>}</td><td>{r.value} <span className="text-muted">{r.unit}</span></td><td className="whitespace-nowrap">{r.as_of}</td></tr>)}
    </tbody></table></div>
  </details>
}
