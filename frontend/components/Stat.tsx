/**
 * A KPI card: the Readout's smaller sibling, for the supporting figures that
 * are scanned as a field rather than read one at a time. `note` is optional
 * and says what the figure means, never what it is worth.
 */
export function Kpi({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  note?: React.ReactNode;
  tone?: "positive" | "signal";
}) {
  const colour =
    tone === "positive" ? "text-positive" : tone === "signal" ? "text-signal" : "text-foreground";
  return (
    <div className="kpi">
      <span className="legend">{label}</span>
      <p className={`kpi-val ${colour}`}>{value}</p>
      {note && <p className="kpi-note">{note}</p>}
    </div>
  );
}

/**
 * The 40-step tick scale beneath a readout: lit teal for the clean share, lit
 * red for the fault, unlit for headroom. `on` and `risk` are step counts the
 * caller has already derived from figures the API computed.
 */
export function Ticks({ on, risk = 0 }: { on: number; risk?: number }) {
  return (
    <div className="ticks" aria-hidden>
      {Array.from({ length: 40 }, (_, i) => (
        <span
          key={i}
          className={"tick " + (i < on ? "tick-on" : i < on + risk ? "tick-risk" : "")}
        />
      ))}
    </div>
  );
}

/** The framed numeric display. */
export function Readout({
  label,
  value,
  sub,
  tone,
  ticks,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "positive" | "signal";
  ticks?: { on: number; risk?: number };
}) {
  const colour =
    tone === "positive" ? "text-positive" : tone === "signal" ? "text-signal" : "text-foreground";
  return (
    <div className="readout">
      <span className="legend">{label}</span>
      <p className={`readout-val ${colour}`}>{value}</p>
      {ticks && <Ticks on={ticks.on} risk={ticks.risk} />}
      {sub && <p className="readout-sub">{sub}</p>}
    </div>
  );
}
