import { cn } from '@/lib/cn'

interface Props {
  title: string
  subtitle?: React.ReactNode
  /** 标题右侧、subtitle 之前的额外节点(如状态徽标) */
  titleExtra?: React.ReactNode
  right?: React.ReactNode
  className?: string
}

export function PageHeader({ title, subtitle, titleExtra, right, className }: Props) {
  return (
    <header
      className={cn(
        'px-4 sm:px-5 pt-3 pb-2 border-b border-border flex flex-wrap items-center justify-between gap-x-4 gap-y-2',
        className,
      )}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <h1 className="text-lg font-semibold tracking-tight break-words">{title}</h1>
        {titleExtra}
        {subtitle && <span className="min-w-0 text-xs leading-relaxed text-muted break-words">{subtitle}</span>}
      </div>
      {right}
    </header>
  )
}
