export function OracleGraphic({ small = false }: { small?: boolean }) {
  return (
    <div className={`oracle-graphic ${small ? 'oracle-small' : ''}`} aria-hidden="true">
      <svg viewBox="0 0 520 420" fill="none">
        <defs>
          <pattern id="diagram-grid" width="32" height="32" patternUnits="userSpaceOnUse">
            <path d="M32 0H0V32" stroke="currentColor" strokeWidth=".5" opacity=".18" />
          </pattern>
          <pattern
            id="diagram-hatch"
            width="7"
            height="7"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <path d="M0 0v7" stroke="currentColor" strokeWidth="1" opacity=".3" />
          </pattern>
        </defs>
        <path fill="url(#diagram-grid)" d="M30 20h460v360H30z" />
        <circle cx="260" cy="202" r="155" stroke="currentColor" opacity=".15" />
        <circle
          cx="260"
          cy="202"
          r="125"
          stroke="currentColor"
          opacity=".15"
          strokeDasharray="2 7"
        />
        <path d="M55 202h410M260 18v368" stroke="currentColor" opacity=".18" />
        <path
          d="m260 69 120 69v138l-120 69-120-69V138Z"
          fill="url(#diagram-hatch)"
          stroke="currentColor"
          opacity=".4"
        />
        <path
          d="m260 94 98 56v113l-98 57-98-57V150Z"
          fill="var(--paper)"
          stroke="currentColor"
          strokeWidth="1.4"
        />
        <path
          d="m260 94 98 56-98 57-98-57Z"
          fill="var(--accent)"
          stroke="currentColor"
          strokeWidth="1.4"
        />
        <path
          d="m162 150 98 57v113l-98-57Z"
          fill="var(--panel)"
          stroke="currentColor"
          strokeWidth="1.4"
        />
        <path d="m260 207 98-57v113l-98 57Z" fill="var(--ink)" stroke="currentColor" />
        <path d="m198 160 62-36 27 16-62 35zm0 27 62-36 27 16-62 35z" fill="var(--ink)" />
        <path d="m287 221 45-26v13l-45 26zm0 30 45-26v13l-45 26z" fill="var(--accent)" />
        <path d="M48 95h49v49M423 262v49h49M193 375h134" stroke="currentColor" strokeWidth="1.2" />
        <path d="M40 80h16M48 72v16M463 325h16M471 317v16" stroke="currentColor" />
        <circle cx="392" cy="119" r="4" fill="var(--ink)" />
        <path d="m397 115 30-29h47M130 288l-30 33H47" stroke="currentColor" opacity=".5" />
        <text x="410" y="76" fill="currentColor" fontSize="9" letterSpacing="2">
          SOURCE
        </text>
        <text x="48" y="341" fill="currentColor" fontSize="9" letterSpacing="2">
          KNOWLEDGE
        </text>
        <text x="205" y="396" fill="currentColor" fontSize="9" letterSpacing="4">
          TRACE TO ORIGIN
        </text>
      </svg>
    </div>
  )
}
