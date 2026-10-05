// StockFund 原创标记：两段相扣的流线构成抽象 S。
// 透明底与 currentColor 保留各页面现有主题和品牌色。
interface LogoProps {
  className?: string
  size?: number
  style?: React.CSSProperties
}

export function Logo({ className, size = 32, style }: LogoProps) {
  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      fill="none"
      className={className}
      style={style}
      role="img"
      aria-label="stockfund-panel"
    >
      <path
        d="M47 10H27C15.954 10 7 18.954 7 30V34H19V30C19 25.582 22.582 22 27 22H41L47 10Z"
        fill="currentColor"
      />
      <path
        d="M17 54H37C48.046 54 57 45.046 57 34V30H45V34C45 38.418 41.418 42 37 42H23L17 54Z"
        fill="currentColor"
      />
    </svg>
  )
}
