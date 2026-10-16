import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { TechnicalSummary } from './TechnicalSummary'
import type { TechnicalSummaryBar } from '@/lib/technicalSummary'

describe('TechnicalSummary', () => {
  it('labels the context and keeps the research-only boundary visible', () => {
    const html = renderToStaticMarkup(
      <TechnicalSummary rows={[]} contextLabel="当前日线研究快照" symbol="000001.SZ" />,
    )

    expect(html).toContain('当前日线研究快照')
    expect(html).toContain('研究技术状态，不是执行信号')
    expect(html).toContain('不汇总为买卖结论')
    expect(html).toContain('不足：至少需要 60 根有效 K 线。')
    expect(html).toContain('有效 K 线不足，无法计算区间表现。')
  })

  it('renders measured range performance and per-item warm-up states', () => {
    const rows: TechnicalSummaryBar[] = Array.from({ length: 15 }, (_, index) => {
      const close = 20
      return {
        time: '2024-01-' + String(index + 1).padStart(2, '0'),
        open: close,
        high: close + 1,
        low: close - 1,
        close,
      }
    })
    const html = renderToStaticMarkup(<TechnicalSummary rows={rows} />)

    expect(html).toContain('RSI14')
    expect(html).toContain('区间内（30–70） · 50.00')
    expect(html).toContain('首收至末收')
    expect(html).toContain('0.00%')
    expect(html).toContain('最大回撤')
    expect(html).toContain('2024-01-01 至 2024-01-15')
  })

  it('retains small nonzero MACD values independently of price tick precision', () => {
    const rows = Array.from({ length: 80 }, (_, index) => {
      const close = 10 + 0.001 * Math.sin(index / 3)
      return { open: close, high: close + 0.01, low: close - 0.01, close }
    })
    const html = renderToStaticMarkup(<TechnicalSummary rows={rows} symbol="600519.SH" />)
    expect(html).toContain('DIF 0.000109177')
    expect(html).toContain('DEA -0.00004419')
    expect(html).toContain('柱 0.000306735')
    expect(html).not.toContain('DIF 0.00 · DEA -0.00')
  })
})
