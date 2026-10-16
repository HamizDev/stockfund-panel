import { useEffect, useMemo, useRef } from 'react'
import * as echarts from 'echarts'
import type { PaperNavItem } from '@/lib/api'
import { paperNavChartData } from '@/lib/paperNavChartData'

export function PaperNavChart({ nav }: { nav: PaperNavItem[] }) {
  const ref = useRef<HTMLDivElement>(null)
  const data = useMemo(() => paperNavChartData(nav), [nav])
  useEffect(() => {
    if (!ref.current || !data.verifiedCount) return
    const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' })
    const render = () => {
      const dark = document.documentElement.classList.contains('dark')
      const text = dark ? '#8E8E96' : '#52525B'
      const line = dark ? '#353539' : '#E4E4E7'
      chart.setOption({
        animation: false, backgroundColor: 'transparent',
        grid: { left: 12, right: 16, top: 30, bottom: 28, containLabel: true },
        tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(4) : '—' },
        xAxis: { type: 'category', boundaryGap: false, data: data.dates, axisLine: { lineStyle: { color: line } }, axisLabel: { color: text, fontSize: 10 } },
        yAxis: { type: 'value', scale: true, axisLabel: { color: text, fontSize: 10, formatter: (value: number) => value.toFixed(2) }, splitLine: { lineStyle: { color: line } } },
        series: [
          { name: '虚拟账户', type: 'line', data: data.equity, showSymbol: data.verifiedCount === 1, symbolSize: 5, connectNulls: false, lineStyle: { color: '#8B5CF6', width: 2 }, itemStyle: { color: '#8B5CF6' }, areaStyle: { color: 'rgba(139,92,246,.08)' } },
          ...(data.hasBenchmark ? [{ name: '沪深300', type: 'line', data: data.benchmark, showSymbol: false, connectNulls: false, lineStyle: { color: text, width: 1.5, type: 'dashed' } }] : []),
        ],
        legend: { top: 0, right: 0, textStyle: { color: text, fontSize: 10 }, itemWidth: 14 },
      }, true)
    }
    render()
    const resize = new ResizeObserver(() => chart.resize())
    resize.observe(ref.current)
    const theme = new MutationObserver(render)
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => { resize.disconnect(); theme.disconnect(); chart.dispose() }
  }, [data])
  if (!data.verifiedCount) return <p className="py-10 text-center text-xs text-muted">暂无完整定版净值 — 等待报价核对</p>
  return <div>
    <p className="mb-1 text-[11px] text-muted">首个完整净值 = 100 · 完整 {data.verifiedCount}/{data.totalCount} 日{!data.hasBenchmark ? ' · 基准缺起始报价，暂不对比' : ''}</p>
    <div ref={ref} className="h-48 w-full" role="img" aria-label="完整定版净值曲线，首个完整日归一化为100" />
    {data.verifiedCount < data.totalCount && <p className="mt-1 text-[11px] text-warning">缺失或待核对日期保留断点。</p>}
  </div>
}
