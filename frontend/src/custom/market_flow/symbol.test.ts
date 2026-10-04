import { describe, expect, it } from 'vitest'
import { toSuffixed } from './symbol'

describe('market-flow stock symbol normalization', () => {
  it.each([
    ['600036.SH', '600036.SH'],
    ['000001.SZ', '000001.SZ'],
    ['430047.BJ', '430047.BJ'],
    ['600036.sh', '600036.SH'],
    ['159915.sz', '159915.SZ'],
    ['920001.bj', '920001.BJ'],
  ])('keeps one valid exchange suffix: %s', (input, expected) => {
    expect(toSuffixed(input)).toBe(expected)
  })

  it.each([
    ['600036', '600036.SH'],
    ['900901', '900901.SH'],
    ['000001', '000001.SZ'],
    ['159915', '159915.SZ'],
    ['510300', '510300.SH'],
    ['560010', '560010.SH'],
    ['588000', '588000.SH'],
    ['430047', '430047.BJ'],
    ['830799', '830799.BJ'],
    ['920001', '920001.BJ'],
  ])('infers only the expected market for raw code %s', (input, expected) => {
    expect(toSuffixed(input)).toBe(expected)
  })

  it.each([
    '600036.SH.SH',
    '600036.SSE',
    '123456',
    '12345',
    'ABCDEF',
  ])('does not append a guessed suffix to unsupported input: %s', input => {
    expect(toSuffixed(input)).toBe(input)
  })
})
