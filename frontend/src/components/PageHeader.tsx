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
  const responsiveHeaderClassName = className?.replace(/(^|\s)sm:(!?(?:pl|px)-\S+)/g, '$1md:$2')
  const mobileRightPadding = responsiveHeaderClassName?.split(/\s+/).reverse()
    .find(token => !token.includes(':') && /^(?:pr|px)-/.test(token))
    ?.replace(/^px-/, 'pr-')

  return (
    <header
      className={cn(
        'pr-4 md:pl-5 md:pr-5 pt-3 pb-2 border-b border-border flex flex-wrap items-center justify-between gap-x-4 gap-y-2',
        responsiveHeaderClassName,
        'pl-14',
        mobileRightPadding,
      )}
    >
      <div className="flex min-w-0 max-w-full flex-wrap items-center gap-x-2 gap-y-1">
        <h1 className="text-lg font-semibold tracking-tight break-words">{title}</h1>
        {titleExtra}
        {subtitle && <span className="min-w-0 text-xs leading-relaxed text-muted break-words">{subtitle}</span>}
      </div>
      {right && (
        <div className="ml-auto flex min-w-0 max-w-full flex-wrap items-center justify-end gap-x-2 gap-y-1 [&>div]:min-w-0 [&>div]:max-w-full [&>div]:flex-wrap">
          {right}
        </div>
      )}
    </header>
  )
}
