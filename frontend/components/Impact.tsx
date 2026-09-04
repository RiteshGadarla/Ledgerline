import { InfoDot } from "@/components/InfoDot";
import { SECONDS_PER_MANUAL_MATCH, formatDuration, type RunImpact } from "@/lib/impact";
import { formatRupees } from "@/lib/money";

/**
 * What the run was worth, in the units a finance lead thinks in.
 *
 * This sits above the rates on purpose. Every other figure on this surface
 * says how *right* the engine was; none of them say what it did for you, and
 * a percentage cannot -- 76.4% of an unstated payment total could be three
 * chains or three hundred. The four figures here are the same run, read as
 * work done, money moved, time returned, and work still outstanding.
 *
 * The last of those is not decoration. A panel that reported only the wins
 * would be marketing; the exceptions column is what makes the other three
 * believable, and it is deliberately given the same weight.
 */
export function Impact({ impact }: { impact: RunImpact }) {
  const cleared = impact.clearedWithoutAHuman;
  const share = impact.paymentsTotal > 0 ? cleared / impact.paymentsTotal : 0;

  return (
    <section className="panel">
      <header className="panel-head">
        <h3 className="legend legend-hi">What this run was worth</h3>
        <span className="ml-auto">
          <InfoDot label="How impact is calculated">
            Two of these are counted, and two are derived from them.
            <span className="mt-2 block">
              <b className="text-foreground">Cleared without a human</b> and{" "}
              <b className="text-foreground">rupees cleared</b> are counts: the payments that ended
              up in a verified chain, and the settlement value behind them. An assisted match is
              included because it was recomputed in integer paise before it was written, exactly
              like an automatic one.
            </span>
            <span className="mt-2 block">
              <b className="text-foreground">Time returned</b> assumes{" "}
              {SECONDS_PER_MANUAL_MATCH} seconds to tie out one chain by hand — pull the settlement,
              find its payments, match them to invoices, then find the credit on the statement. It
              is a deliberately conservative figure, and it is an assumption rather than a
              measurement.
            </span>
            <span className="mt-2 block">
              Open exceptions are never counted as time saved. They are the work that is left.
            </span>
          </InfoDot>
        </span>
      </header>

      <div className="grid gap-px bg-hairline [grid-template-columns:repeat(auto-fit,minmax(180px,1fr))]">
        <Figure
          label="Cleared without a human"
          value={cleared.toLocaleString()}
          sub={`of ${impact.paymentsTotal.toLocaleString()} payments — ${(share * 100).toFixed(1)}%`}
          tone="positive"
        />
        <Figure
          label="Time returned"
          value={formatDuration(impact.secondsSaved)}
          sub={`at ${SECONDS_PER_MANUAL_MATCH}s per chain, matched by hand`}
          tone="positive"
        />
        <Figure
          label="Rupees cleared"
          value={formatRupees(impact.amountCleared)}
          sub="Settlement value behind the verified chains."
          tone="positive"
        />
        <Figure
          label="Still needs a human"
          value={impact.stillNeedsAHuman.toLocaleString()}
          sub={`${formatRupees(impact.amountAtRisk)} held behind them.`}
          tone={impact.stillNeedsAHuman > 0 ? "signal" : undefined}
        />
      </div>
    </section>
  );
}

function Figure({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub: string;
  tone?: "positive" | "signal";
}) {
  const colour =
    tone === "positive" ? "text-positive" : tone === "signal" ? "text-signal" : "text-foreground";
  return (
    <div className="bg-surface px-3.5 py-3">
      <span className="legend">{label}</span>
      <p className={`mt-1 text-[clamp(1.375rem,2.4vw,1.75rem)] leading-none font-semibold tracking-[-0.02em] tabular ${colour}`}>
        {value}
      </p>
      <p className="mt-1.5 text-[12.5px] leading-snug text-faint">{sub}</p>
    </div>
  );
}
