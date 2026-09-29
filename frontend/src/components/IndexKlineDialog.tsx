import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { Modal } from '@/components/Modal'
import { CandlestickChart, type OHLC } from '@/components/CandlestickChart'
import { api } from '@/lib/api'

interface Props {
  code: string
  name: string
  onClose: () => void
}

export function IndexKlineDialog({ code, name, onClose }: Props) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['industry-kline', code],
    queryFn: () => api.industryKline(code, 60),
  })

  const ohlc: OHLC[] = (data?.kline ?? []).map((r: any) => ({
    date: new Date(r.date_ms).toISOString().slice(0, 10),
    open: r.open_price,
    high: r.high_price,
    low: r.low_price,
    close: r.close_price,
    volume: r.volume,
  }))

  return (
    <Modal onClose={onClose}>
      <div className="w-[90vw] max-w-2xl rounded-card border border-border bg-surface p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-medium">{name}指数走势</h3>
          <button onClick={onClose} className="rounded p-1 hover:bg-elevated">
            <X size={16} />
          </button>
        </div>
        {isLoading && <div className="py-8 text-center text-xs text-muted">加载中...</div>}
        {error && <div className="py-8 text-center text-xs text-bear">加载失败</div>}
        {ohlc.length > 0 && <CandlestickChart data={ohlc} height={300} />}
      </div>
    </Modal>
  )
}
