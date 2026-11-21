export function Brand({
  compact = false,
  onDark = false,
}: {
  compact?: boolean
  onDark?: boolean
}) {
  return (
    <div className={`brand ${compact ? 'brand-compact' : ''} ${onDark ? 'brand-on-dark' : ''}`}>
      <img className="brand-mark" src="/favicon.svg" alt="" />
      <div className="brand-text">
        <span className="brand-name">FinOracle</span>
        {!compact && <span className="brand-subtitle">金融制度知识工作台</span>}
      </div>
    </div>
  )
}
