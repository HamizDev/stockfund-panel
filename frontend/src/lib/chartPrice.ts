export function chartPriceDigits(symbol?: string) {
  if (symbol && /^(5\d{5}\.SH|1[56]\d{4}\.SZ)$/.test(symbol)) return 3
  // Without instrument context, retain fund price ticks rather than rounding them away.
  return symbol ? 2 : 3
}
export function chartPrice(value: unknown, symbol?: string): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(chartPriceDigits(symbol)) : '—'
}
