import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { INSTRUMENT_VIEW_GC_MS, useInstrumentViewCache } from '@/lib/useInstrumentViewCache'
import type { ResearchSentimentSource } from './types'
import { formatGlobalMarketChange, formatGlobalMarketTime } from './marketFormatters'

const SENTIMENT_CACHE_MS = 60 * 60_000
const SOURCE_OPTIONS: { value: ResearchSentimentSource; label: string }[] = [
  { value: 'reddit', label: 'Reddit' },
  { value: 'news', label: '新闻' },
  { value: 'x', label: 'X' },
  { value: 'polymarket', label: 'Polymarket' },
]
const SETTINGS_HREF = '/settings?tab=data-sources&highlight=global-research'

function displayNumber(value: number | null | undefined, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return value.toLocaleString('en-US', { maximumFractionDigits: digits })
}

function displayUsd(value: number | null | undefined, compact = false): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: compact ? 'compact' : 'standard',
    maximumFractionDigits: compact ? 2 : 4,
  }).format(value)
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请重试'
}

export function ResearchSupplementPanel({ symbol }: { symbol: string }) {
  const normalizedSymbol = symbol.trim().toUpperCase()
  const isIndex = normalizedSymbol.startsWith('^')
  const [sentimentSource, setSentimentSource] = useState<ResearchSentimentSource>('news')

  useInstrumentViewCache('global-market-us', normalizedSymbol)

  const sourcesQuery = useQuery({
    queryKey: QK.globalResearchSources,
    queryFn: ({ signal }) => api.globalResearchSources(signal),
    staleTime: 30_000,
    retry: false,
    refetchOnWindowFocus: false,
    enabled: !isIndex,
  })
  const finnhubConfigured = sourcesQuery.data?.sources.some(source => source.provider === 'finnhub' && source.configured) ?? false
  const adanosConfigured = sourcesQuery.data?.sources.some(source => source.provider === 'adanos' && source.configured) ?? false

  const supplementQuery = useQuery({
    queryKey: QK.globalResearchSupplement(normalizedSymbol),
    queryFn: ({ signal }) => api.globalResearchSupplement(normalizedSymbol, signal),
    enabled: !isIndex && finnhubConfigured,
    staleTime: 60_000,
    gcTime: INSTRUMENT_VIEW_GC_MS,
    retry: false,
    refetchOnWindowFocus: false,
    meta: { instrumentView: 'global-market-us', symbol: normalizedSymbol },
  })
  const sentimentQuery = useQuery({
    queryKey: QK.globalResearchSentiment(normalizedSymbol, sentimentSource),
    queryFn: ({ signal }) => api.globalResearchSentiment(normalizedSymbol, sentimentSource, signal),
    enabled: false,
    staleTime: SENTIMENT_CACHE_MS,
    gcTime: INSTRUMENT_VIEW_GC_MS,
    retry: false,
    refetchOnWindowFocus: false,
    meta: { instrumentView: 'global-market-us', symbol: normalizedSymbol },
  })

  const supplement = supplementQuery.data?.market === 'us' && supplementQuery.data.symbol.toUpperCase() === normalizedSymbol
    ? supplementQuery.data
    : undefined
  const sentiment = sentimentQuery.data?.market === 'us'
    && sentimentQuery.data.symbol.toUpperCase() === normalizedSymbol
    && sentimentQuery.data.source === sentimentSource
    ? sentimentQuery.data
    : undefined
  const showSettingsLink = !sourcesQuery.isPending && (!finnhubConfigured || !adanosConfigured)

  async function requestSentiment() {
    if (!adanosConfigured || isIndex || sentimentQuery.isFetching) return
    if (sentiment && !sentimentQuery.isStale) return
    await sentimentQuery.refetch()
  }

  return (
    <section aria-label="美股补充研究" className="rounded-card border border-border/60 bg-surface/80 p-2.5 sm:p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-xs font-semibold text-foreground">美股补充研究</h2>
          <p className="mt-0.5 text-[10px] text-muted">{normalizedSymbol} · 独立于主图行情</p>
        </div>
        {supplement?.cached && <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-secondary">缓存数据</span>}
      </div>

      {isIndex ? (
        <p role="status" className="mt-2.5 rounded-md border border-border px-2.5 py-2 text-xs text-muted">指数暂未支持补充数据。</p>
      ) : (
        <>
          {sourcesQuery.isPending && <p role="status" className="mt-2.5 text-xs text-muted">正在检查数据源设置…</p>}
          {sourcesQuery.isError && (
            <p role="alert" className="mt-2.5 text-xs text-danger">
              数据源状态读取失败：{errorText(sourcesQuery.error)}
            </p>
          )}

          {finnhubConfigured && (
            <div className="mt-2.5 border-t border-border/60 pt-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-[11px] font-medium text-foreground">补充报价与公司资料</h3>
                {supplement?.fetched_at_ms != null && (
                  <span className="text-[10px] text-muted">获取于 {formatGlobalMarketTime(Math.floor(supplement.fetched_at_ms / 1_000), 'America/New_York', true)}</span>
                )}
              </div>
              {supplementQuery.isPending && <p role="status" className="mt-1.5 text-xs text-muted">正在加载补充数据…</p>}
              {supplementQuery.isError && <p role="alert" className="mt-1.5 text-xs text-danger">补充数据加载失败：{errorText(supplementQuery.error)}</p>}
              {supplement && (
                <>
                  <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <Metric label="最新报价" value={displayUsd(supplement.quote?.price)} />
                    <Metric
                      label="涨跌幅"
                      value={formatGlobalMarketChange(supplement.quote?.change_pct)}
                      tone={supplement.quote?.change_pct == null ? 'neutral' : supplement.quote.change_pct > 0 ? 'up' : supplement.quote.change_pct < 0 ? 'down' : 'neutral'}
                    />
                    <Metric label="昨收" value={displayUsd(supplement.quote?.previous_close)} />
                    <Metric
                      label="报价时间（纽约）"
                      value={supplement.quote?.as_of_ms == null
                        ? '—'
                        : formatGlobalMarketTime(Math.floor(supplement.quote.as_of_ms / 1_000), 'America/New_York', true)}
                    />
                  </div>
                  {supplement.warnings.map((warning, index) => (
                    <p key={`${warning}-${index}`} className="mt-1.5 text-[10px] leading-4 text-warning">提示：{warning}</p>
                  ))}
                  <details className="mt-2 rounded-md border border-border/70 bg-base/40 px-2.5 py-2">
                    <summary className="cursor-pointer text-[11px] font-medium text-secondary">公司资料与新闻（{supplement.news.length}）</summary>
                    <div className="mt-2 space-y-2">
                      <div className="grid gap-1 text-[10px] text-muted sm:grid-cols-3">
                        <span>公司：{supplement.profile?.name || '—'}</span>
                        <span>行业：{supplement.profile?.industry || '—'}</span>
                        <span>市值：{displayUsd(supplement.profile?.market_cap_usd, true)}</span>
                      </div>
                      {supplement.news.length === 0
                        ? <p className="text-[10px] text-muted">暂无新闻摘要。</p>
                        : <ul className="space-y-1.5">
                          {supplement.news.map((item, index) => (
                            <li key={`${item.url}-${index}`} className="rounded border border-border/60 p-2">
                              <a href={item.url} target="_blank" rel="noopener noreferrer" className="text-[11px] font-medium text-accent hover:underline">
                                {item.headline || '—'}
                              </a>
                              <p className="mt-0.5 text-[10px] text-muted">{formatGlobalMarketTime(Math.floor(item.published_at_ms / 1_000), 'America/New_York', true)}</p>
                              {item.summary && <p className="mt-1 whitespace-pre-wrap break-words text-[10px] leading-4 text-secondary">{item.summary}</p>}
                            </li>
                          ))}
                        </ul>}
                    </div>
                  </details>
                </>
              )}
            </div>
          )}

          {adanosConfigured && (
            <div className="mt-2.5 border-t border-border/60 pt-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-[11px] font-medium text-foreground">舆情摘要</h3>
                <div className="flex items-center gap-1.5">
                  <label htmlFor="research-sentiment-source" className="sr-only">舆情来源</label>
                  <select
                    id="research-sentiment-source"
                    value={sentimentSource}
                    onChange={event => setSentimentSource(event.target.value as ResearchSentimentSource)}
                    className="h-8 rounded-btn border border-border bg-base px-2 text-[11px] text-foreground outline-none focus:border-accent/50"
                  >
                    {SOURCE_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                  <button
                    type="button"
                    aria-label="手动查询所选来源舆情"
                    onClick={() => void requestSentiment()}
                    disabled={sentimentQuery.isFetching || (!!sentiment && !sentimentQuery.isStale)}
                    className="min-h-8 rounded-btn border border-border bg-surface px-2.5 py-1 text-[11px] text-secondary transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {sentimentQuery.isFetching ? '查询中…' : sentiment && !sentimentQuery.isStale ? '缓存有效' : '手动查询'}
                  </button>
                </div>
              </div>
              <p className="mt-1 text-[10px] text-muted">每次新查询只请求所选来源一次；结果最多缓存 1 小时，切换标的或缓存淘汰后可能重新取数。</p>
              {sentimentQuery.isError && <p role="alert" className="mt-1.5 text-xs text-danger">舆情查询失败：{errorText(sentimentQuery.error)}</p>}
              {sentiment && (
                <>
                  <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <Metric label="热度分数" value={displayNumber(sentiment.buzz_score)} />
                    <Metric label="看涨占比" value={sentiment.bullish_pct == null ? '—' : `${displayNumber(sentiment.bullish_pct)}%`} />
                    <Metric label={sentiment.metric === 'trades' ? '交易数' : '提及次数'} value={displayNumber(sentiment.mentions, 0)} />
                    <Metric label="统计天数" value={sentiment.period_days == null ? '—' : `${displayNumber(sentiment.period_days, 0)} 天`} />
                  </div>
                  {sentiment.warnings.map((warning, index) => (
                    <p key={`${warning}-${index}`} className="mt-1.5 text-[10px] leading-4 text-warning">提示：{warning}</p>
                  ))}
                  {sentiment.cached && <p className="mt-1.5 text-[10px] text-muted">服务端缓存结果</p>}
                  {sentiment.fetched_at_ms != null && <p className="mt-1 text-[10px] text-muted">获取于 {formatGlobalMarketTime(Math.floor(sentiment.fetched_at_ms / 1_000), 'America/New_York', true)}</p>}
                </>
              )}
            </div>
          )}

          {showSettingsLink && (
            <div className="mt-2.5 border-t border-border/60 pt-2.5 text-[10px] text-muted">
              <span>{sourcesQuery.isError || !sourcesQuery.data ? '请检查本机数据源设置。' : '未配置的数据源可在设置中管理。'}</span>{' '}
              <Link to={SETTINGS_HREF} className="text-accent hover:underline">打开数据源设置</Link>
            </div>
          )}
          {sourcesQuery.data && !finnhubConfigured && !adanosConfigured && (
            <p role="status" className="mt-2.5 rounded-md border border-border px-2.5 py-2 text-xs text-muted">尚未配置补充数据源。</p>
          )}
        </>
      )}
    </section>
  )
}

function Metric({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'neutral' | 'up' | 'down' }) {
  const color = tone === 'up' ? 'text-bull' : tone === 'down' ? 'text-bear' : 'text-foreground'
  return (
    <div className="min-w-0 rounded-md border border-border/70 bg-base/40 px-2 py-1.5">
      <div className="text-[10px] text-muted">{label}</div>
      <div className={`mt-0.5 break-words text-xs font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  )
}
