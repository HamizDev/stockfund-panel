import { useState } from 'react'
import { Sparkles } from 'lucide-react'
import { fetchAssistantStatus } from '@/custom/assistant/client'
import { openAssistant, sendResearchMessage, useAssistantStore } from '@/custom/assistant/store'

export function ResearchAIButton({ label, prompt, disabled = false }: { label: string; prompt: string; disabled?: boolean }) {
  const { sending } = useAssistantStore()
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')
  const run = async () => {
    setChecking(true)
    setError('')
    if (prompt.length > 4000) { setError('摘要过长，请减少研究范围后重试。'); setChecking(false); return }
    try {
      const status = await fetchAssistantStatus()
      if (!status.configured || !status.supports_tools) {
        openAssistant()
        setError('请先在设置中配置可用的 AI 助手。')
        return
      }
      if (!sendResearchMessage(prompt)) setError('AI 正在回答其他问题，请完成后再试。')
    } catch {
      setError('无法检查 AI 助手连接，请稍后重试。')
    } finally { setChecking(false) }
  }
  return <div className="inline-flex max-w-full flex-col gap-1">
    <button type="button" onClick={run} disabled={disabled || sending || checking}
      title="新建独立对话，仅发送此页摘要，不附带先前聊天。使用已配置模型的额度，不自动下单。"
      className="inline-flex items-center justify-center gap-1.5 rounded-btn border border-accent/30 bg-accent/10 px-3 py-1.5 text-xs text-accent hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-40">
      <Sparkles className="h-3.5 w-3.5" />{checking ? '检查 AI 连接…' : label}
    </button>
    {error && <span role="alert" className="text-[11px] text-warning">{error}</span>}
  </div>
}
