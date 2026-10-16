import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const source = readFileSync(
  new URL('./DimensionMembersDialog.tsx', import.meta.url),
  'utf8',
)

describe('DimensionMembersDialog member identity', () => {
  it('places the board tag after the stock name and keeps the code on its own line', () => {
    // Anchor the identity structure, not utility classes, so visual restyling does not invalidate the contract.
    const identity = source.match(
      /<span className="min-w-0">\s*<span className="flex[^"]*">([\s\S]*?)<\/span>\s*<span className="block font-mono[^"]*">\{row\.symbol\}<\/span>/,
    )

    expect(identity, 'the member row should keep name, board tag, and code in a two-line identity block').not.toBeNull()
    expect(identity![1].indexOf('{row.name || row.symbol}'))
      .toBeLessThan(identity![1].indexOf('{board &&'))
  })
})
