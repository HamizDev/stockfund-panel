export function Skeleton({ w = 'w-full', h = 'h-3.5', rounded = 'rounded-sm', className = '' }: {
  w?: string; h?: string; rounded?: string; className?: string
}) {
  return (
    <div className={`${w} ${h} ${rounded} bg-elevated/60 animate-pulse ${className}`} />
  )
}
