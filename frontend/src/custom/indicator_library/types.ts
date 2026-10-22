export type ScriptKind = 'all' | 'indicator' | 'strategy' | 'library'
export interface IndicatorRecipe {
  id: string
  name: string
  kind: string
  description: string
  inputs: string
  entry: string
  exit: string
  caution: string
}
export interface IndicatorCatalog {
  topics: { id: string; name: string; aliases: string[] }[]
  recipes: IndicatorRecipe[]
}
export interface PublicScript {
  id: string
  name: string
  author: string
  kind: Exclude<ScriptKind, 'all'>
  version: string
  description: string
  url: string | null
  source_cached: boolean
  cached_version?: string | null
}
export interface IndicatorSearch {
  items: PublicScript[]
  page: number
  has_more: boolean
  cached: boolean
  offline: boolean
  fetched_at_ms: number | null
  topic: string
  note: string
}
export interface IndicatorSource {
  id: string
  name: string
  version: string
  pine_version: number | null
  source: string
  bytes: number
  cached: boolean
  fetched_at_ms: number
}
export interface IndicatorSearchParams {
  q: string
  author: string
  kind: ScriptKind
  page: number
  offline: boolean
}
