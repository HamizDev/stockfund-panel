import { useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, ExternalLink, KeyRound, Loader2, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

type Provider = 'finnhub' | 'adanos'

const PROVIDERS: Array<{
  id: Provider
  name: string
  registrationUrl: string
  description: string
}> = [
  {
    id: 'finnhub',
    name: 'Finnhub',
    registrationUrl: 'https://finnhub.io/register',
    description: '按需补充美股报价、公司资料和新闻，不会自动轮询。',
  },
  {
    id: 'adanos',
    name: 'Adanos',
    registrationUrl: 'https://adanos.com/register',
    description: '免费额度为每月 250 次；保存验证会消耗 1 次。研究页每次手动查询只请求一个来源，结果最多缓存 1 小时，不会自动轮询。',
  },
]

const EMPTY_KEYS: Record<Provider, string> = { finnhub: '', adanos: '' }

/** Credentials are entered only for the local backend and never persisted in browser storage. */
export function ResearchSourceSettings() {
  const queryClient = useQueryClient()
  const [inputs, setInputs] = useState(EMPTY_KEYS)
  const [busy, setBusy] = useState<Provider | null>(null)
  const [confirmClear, setConfirmClear] = useState<Provider | null>(null)
  const [feedback, setFeedback] = useState<Partial<Record<Provider, { kind: 'success' | 'error'; text: string }>>>({})

  const sources = useQuery({
    queryKey: QK.globalResearchSources,
    queryFn: ({ signal }) => api.globalResearchSources(signal),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  })

  const refreshAfterKeyChange = async () => {
    await queryClient.invalidateQueries({ queryKey: QK.globalResearchSources })
    queryClient.removeQueries({ queryKey: ['global-research'] })
  }

  const setInput = (provider: Provider, value: string) => {
    setInputs(previous => ({ ...previous, [provider]: value }))
  }

  const saveKey = async (event: FormEvent<HTMLFormElement>, provider: Provider) => {
    event.preventDefault()
    const key = inputs[provider].trim()
    if (!key || busy) {
      if (!key) setInput(provider, '')
      return
    }

    setBusy(provider)
    setConfirmClear(null)
    setFeedback(previous => ({ ...previous, [provider]: undefined }))
    try {
      await api.saveGlobalResearchKey(provider, key)
      await refreshAfterKeyChange()
      setFeedback(previous => ({ ...previous, [provider]: { kind: 'success', text: 'Key 已验证并保存。' } }))
    } catch {
      // Keep provider/network error text out of the UI; it can contain request details.
      setFeedback(previous => ({ ...previous, [provider]: { kind: 'error', text: '验证或保存失败。请检查 Key 和服务连接后重试。' } }))
    } finally {
      setInput(provider, '')
      setBusy(null)
    }
  }

  const clearKey = async (provider: Provider) => {
    if (busy) return
    setBusy(provider)
    setInput(provider, '')
    try {
      await api.clearGlobalResearchKey(provider)
      await refreshAfterKeyChange()
      setFeedback(previous => ({ ...previous, [provider]: { kind: 'success', text: '本机保存的 Key 已移除。' } }))
    } catch {
      setFeedback(previous => ({ ...previous, [provider]: { kind: 'error', text: '移除失败，请检查服务连接后重试。' } }))
    } finally {
      setConfirmClear(null)
      setBusy(null)
    }
  }

  return (
    <section className="rounded-card border border-border bg-surface p-5">
      <div className="mb-4 flex items-center gap-2.5">
        <KeyRound className="h-4 w-4 shrink-0 text-secondary" aria-hidden="true" />
        <h2 className="text-sm font-medium text-foreground">美股补充数据与舆情</h2>
      </div>

      <p className="mb-4 rounded-lg border border-border/60 bg-base/40 px-3 py-2.5 text-[11px] leading-relaxed text-secondary">
        Key 保存在本机凭据 JSON 中，不写入浏览器持久缓存；保存验证及后续请求只向对应官方 API 认证。移除本机 Key 后，如果环境变量仍设置了同名 Key，服务会继续使用环境变量。
      </p>

      {sources.isError && (
        <p role="status" className="mb-4 text-xs text-warning">暂时无法读取本机 Key 状态；仍可尝试保存。</p>
      )}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {PROVIDERS.map(provider => {
          const status = sources.data?.sources.find(item => item.provider === provider.id)
          const configured = status?.configured === true
          const busyThisProvider = busy === provider.id
          const message = feedback[provider.id]

          return (
            <article key={provider.id} className="min-w-0 rounded-lg border border-border/70 bg-base/30 p-4">
              <div className="mb-2 flex items-center justify-between gap-2">
                <h3 className="text-sm font-medium text-foreground">{provider.name}</h3>
                <span className={`shrink-0 text-[10px] ${configured ? 'text-accent' : 'text-muted'}`}>
                  {configured ? '已配置' : '未配置'}
                </span>
              </div>
              <p className="min-h-9 text-[11px] leading-relaxed text-muted">{provider.description}</p>
              <a
                href={provider.registrationUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
              >
                {provider.name} 官方注册
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
              </a>

              {configured && status?.masked && (
                <div className="mt-3 flex min-w-0 items-center gap-2 rounded-md border border-border/50 bg-surface/50 px-2.5 py-2">
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-accent" aria-hidden="true" />
                  <span className="shrink-0 text-[10px] text-secondary">当前 Key</span>
                  <code className="min-w-0 truncate font-mono text-[11px] text-muted" data-testid={`${provider.id}-masked-key`}>
                    {status.masked}
                  </code>
                </div>
              )}

              <form onSubmit={event => void saveKey(event, provider.id)} className="mt-3 space-y-2">
                <label className="sr-only" htmlFor={`${provider.id}-api-key`}>{provider.name} API Key</label>
                <input
                  id={`${provider.id}-api-key`}
                  aria-label={`${provider.name} API Key`}
                  type="password"
                  value={inputs[provider.id]}
                  onChange={event => {
                    setInput(provider.id, event.target.value)
                    setFeedback(previous => ({ ...previous, [provider.id]: undefined }))
                  }}
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={configured ? '粘贴新 Key 替换本机 Key' : `粘贴 ${provider.name} API Key`}
                  disabled={busy !== null}
                  className="w-full rounded-input border border-border bg-base px-3 py-2 text-xs font-mono text-foreground transition-colors focus:border-accent focus:outline-none disabled:opacity-50"
                />
                <button
                  type="submit"
                  disabled={busy !== null || !inputs[provider.id].trim()}
                  className="inline-flex min-h-8 w-full items-center justify-center gap-1.5 rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {busyThisProvider ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />}
                  {busyThisProvider ? '验证中...' : '验证并保存'}
                </button>
              </form>

              {configured && (
                <div className="mt-2">
                  {confirmClear === provider.id ? (
                    <div className="rounded-md border border-warning/30 bg-warning/5 p-2.5">
                      <p className="text-[10px] leading-relaxed text-secondary">
                        只清除本机凭据 JSON 中保存的值；环境变量中的同名 Key 不会被删除。
                      </p>
                      <div className="mt-2 flex justify-end gap-2">
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() => setConfirmClear(null)}
                          className="rounded-btn px-2.5 py-1 text-[10px] text-muted hover:text-foreground disabled:opacity-50"
                        >
                          取消
                        </button>
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() => void clearKey(provider.id)}
                          className="rounded-btn bg-danger/15 px-2.5 py-1 text-[10px] font-medium text-danger hover:bg-danger/25 disabled:opacity-50"
                        >
                          {busyThisProvider ? '移除中...' : '确认移除'}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => setConfirmClear(provider.id)}
                      className="inline-flex items-center gap-1 rounded-btn px-2 py-1 text-[10px] text-muted transition-colors hover:text-danger disabled:opacity-50"
                    >
                      <Trash2 className="h-3 w-3" aria-hidden="true" />
                      移除本机 Key
                    </button>
                  )}
                </div>
              )}

              {message && (
                <p role={message.kind === 'error' ? 'alert' : 'status'} className={`mt-2 text-[10px] ${message.kind === 'error' ? 'text-danger' : 'text-accent'}`}>
                  {message.text}
                </p>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}
