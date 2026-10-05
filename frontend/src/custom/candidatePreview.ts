export const DEFAULT_CANDIDATE_PREVIEW_LIMIT = 10

/** Keep the caller's ranking order; searches pass all matching rows through. */
export function previewCandidates<T>(
  items: readonly T[],
  options: { expanded: boolean; searchActive?: boolean; limit?: number },
): T[] {
  if (options.expanded || options.searchActive) return [...items]
  const limit = Number.isFinite(options.limit)
    ? Math.max(0, Math.floor(options.limit ?? DEFAULT_CANDIDATE_PREVIEW_LIMIT))
    : DEFAULT_CANDIDATE_PREVIEW_LIMIT
  return items.slice(0, limit)
}
