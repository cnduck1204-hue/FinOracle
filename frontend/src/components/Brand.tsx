export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? 'brand-compact' : ''}`}>
      <svg className="brand-mark" aria-hidden="true" viewBox="0 0 40 40">
        <path d="M0 0h40v30L30 40H0z" fill="currentColor" />
        <path d="M10 9h23v6H17v6h12v6H17v8h-7z" fill="var(--paper)" />
        <path d="M29 29h4v6h-4z" fill="var(--paper)" />
      </svg>
      <div>
        <span className="brand-name">
          FinOracle<span className="brand-period">.</span>
        </span>
        {!compact && <span className="brand-subtitle">金融制度知识工作台</span>}
      </div>
    </div>
  )
}
