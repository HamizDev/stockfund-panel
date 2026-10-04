const SUFFIXED_SYMBOL = /^(\d{6})\.(SH|SZ|BJ)$/i

type Exchange = 'SH' | 'SZ' | 'BJ'

function inferExchange(code: string): Exchange | null {
  if (/^(?:15|16|20|00|30)/.test(code)) return 'SZ'
  if (/^(?:4|8|92)/.test(code)) return 'BJ'
  if (/^(?:5|6|90)/.test(code)) return 'SH'
  return null
}

/** Keep valid exchange codes, infer known markets only for six-digit raw codes. */
export function toSuffixed(symbol: string): string {
  const value = symbol.trim()
  const suffixed = value.match(SUFFIXED_SYMBOL)
  if (suffixed) return `${suffixed[1]}.${suffixed[2].toUpperCase()}`
  if (!/^\d{6}$/.test(value)) return value

  const exchange = inferExchange(value)
  return exchange ? `${value}.${exchange}` : value
}
