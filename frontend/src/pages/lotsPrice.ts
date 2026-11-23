import { fmtPrice } from '@/lib/format'

/** Keep the precision implied by the authoritative asset type. Unknown types preserve three decimals. */
export function formatLotPrice(value: number | null | undefined, assetType?: string): string {
  return fmtPrice(value, assetType === 'stock' ? 2 : 3)
}
