/**
 * A plate of related figures, read down a column rather than across a field
 * of cards. Grouping them is what makes them legible: eight equal cards in
 * one row say nothing about which figures answer the same question, and a
 * card wide enough to hold "7,983 / 7,983 ms" is mostly empty for "2".
 */
export function StatGroup({
  title,
  info,
  children,
}: {
  title: string;
  /** An "i" beside the heading, for a panel whose figures need their
   *  provenance stated rather than assumed. */
  info?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="panel flex flex-col">
      <header className="panel-head">
        <h3 className="legend legend-hi">{title}</h3>
        {info && <span className="ml-auto">{info}</span>}
      </header>
      <dl className="flex flex-col">{children}</dl>
    </section>
  );
}

/**
 * One figure inside a StatGroup: label and value share a baseline, the note
 * under both says what the figure means, never what it is worth.
 */
export function StatRow({
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
    <div className="stat-row">
      <dt className="legend">{label}</dt>
      <dd className={`stat-row-val ${colour}`}>{value}</dd>
      {note && <p className="stat-row-note">{note}</p>}
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
