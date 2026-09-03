/**
 * The mark: ledger lines on the left, gathered by a single vertical tie-out
 * bar into one settled point on the right. Drawn on a 32px grid with 2px
 * strokes so it stays legible down to about 20px.
 *
 * Flat, not gradient-filled: the strokes take `currentColor` so the mark sits
 * correctly on paper, on the dark rail, and on the dark auth panel without
 * three variants. Only the settled point carries the readout colour.
 */
export function Logo({ size = 26, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      role="img"
      aria-label="Ledgerline"
      className={className}
    >
      <g stroke="currentColor" strokeWidth="2.1" strokeLinecap="round">
        <path d="M6 9h9" />
        <path d="M6 14h6" />
        <path d="M6 19h6" opacity="0.55" />
        <path d="M6 24h9" opacity="0.55" />
        {/* the tie-out */}
        <path d="M20 9v14" opacity="0.4" />
      </g>
      <circle cx="20" cy="16" r="3.2" fill="var(--readout-hi)" />
    </svg>
  );
}

export function Wordmark({ size = 24, className = "" }: { size?: number; className?: string }) {
  return (
    <span className={"inline-flex items-center gap-2.5 " + className}>
      <Logo size={size} />
      <span className="text-[15.5px] font-semibold tracking-[-0.01em]">Ledgerline</span>
    </span>
  );
}
