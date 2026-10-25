export interface EvidenceRow { id: string; symbol: string; field: string; value: number; as_of: string; unit: string; price_basis: string; inputs?: { formula: string; refs: string[] } }
export interface ReportEvidence {
  ledger: { version: number; symbol: string; as_of?: string; facts: EvidenceRow[]; calculations: EvidenceRow[]; limits: string }
  verification: { status: string; checked: number; bound: number; issues: { line: number; number: string; reason: string }[]; unknown_references: string[]; limits: string }
}
export interface ResearchSnapshot { status: 'idle' | 'loading' | 'ready' | 'error'; snapshot: ResearchData | null; stale: boolean; error: string | null; cached_at: number | null }
export interface ResearchData { dataset: string; status: string; symbol?: string; market?: string; source?: string; as_of?: string; fetched_at?: string; unit?: string | Record<string,string>; guard?: string; gaps?: string[]; items: Record<string, unknown>[] }
export interface LibraryRecord { id: string; name: string; [key: string]: unknown }
