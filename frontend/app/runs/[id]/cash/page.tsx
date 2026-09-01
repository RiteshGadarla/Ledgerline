"use client";

import { use } from "react";
import { Readout } from "@/components/Stat";
import { Loading, PanelHead } from "@/components/Surface";
import { formatRupees } from "@/lib/money";
import { ceilingForStacks, percentCss } from "@/lib/scale";
import { useRun } from "@/lib/useRun";

/**
 * Recognised vs blocked is a status pair, and green/red is the one pair that
 * fails: it separates by only ΔE 5.4 under deuteranopia, which makes the
 * chart unreadable for a red-green viewer. Teal against the same red clears
 * ΔE 11.1 in light and 12.0 in dark. Blocked additionally carries a diagonal
 * hatch, so the two series stay separable in greyscale, in print, and under
 * forced colours: colour is never the only encoding.
 */
export default function CashPositionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const run = useRun(id);

  if (run?.state === "failed") return <p className="text-sm text-signal">This run failed: {run.error}</p>;
  if (!run) return <Loading />;
  if (!run.forecast) {
    return <p className="text-sm text-muted">The cash position will appear once the run completes.</p>;
  }

  const { days, unrecognised_cash } = run.forecast;

  // The chart's own axis. Summing a stack is presentation geometry and lives
  // in lib/scale.ts, the one place sanctioned to divide an amount; the page
  // never derives a figure the API did not already compute.
  const ceiling = ceilingForStacks(days.map((d) => [d.recognised, d.blocked]));
  const anyBlocked = days.some((d) => d.blocked > 0);

  return (
    <>
      <section className="panel">
        <PanelHead legend="Projected cash position" note="T+2 WINDOW · THIS CORPUS" />

        {days.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-muted">
            No settlements in this run to project.
          </p>
        ) : (
          <div className="p-4">
            {/* Legend: two series, so identity is never colour alone. */}
            <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-2">
              <span className="flex items-center gap-2 text-[11.5px] text-muted">
                <span aria-hidden className="h-3 w-3 rounded-sm bg-readout-hi" />
                Recognised
              </span>
              <span className="flex items-center gap-2 text-[11.5px] text-muted">
                <span aria-hidden className="hatch h-3 w-3 rounded-sm" />
                Blocked behind an exception
              </span>
            </div>

            <div className="flex h-48 items-end gap-[clamp(0.375rem,1.2vw,1.25rem)]">
              {days.map((day) => {
                const hasBlock = day.blocked > 0;
                return (
                  <div
                    key={day.date}
                    className="group relative flex h-full min-w-0 flex-1 flex-col justify-end"
                  >
                    {/* per-mark hover readout */}
                    <span
                      role="tooltip"
                      className="mono pointer-events-none absolute bottom-full left-1/2 z-10 -translate-x-1/2 -translate-y-1.5 whitespace-nowrap rounded-[3px] bg-rail px-2 py-1 text-[10.5px] text-white opacity-0 transition-opacity group-hover:opacity-100"
                    >
                      {day.date} · {formatRupees(day.recognised)}
                      {hasBlock ? ` · ${formatRupees(day.blocked)} blocked` : ""}
                    </span>

                    {hasBlock && (
                      <span
                        className="hatch mx-auto w-full max-w-16 rounded-t-[3px]"
                        style={{ height: percentCss(day.blocked, ceiling) }}
                      />
                    )}
                    {/* a 2px surface gap between stacked segments */}
                    {hasBlock && <span aria-hidden className="h-0.5 shrink-0" />}
                    <span
                      className={
                        "mx-auto w-full max-w-16 bg-readout-hi " + (hasBlock ? "" : "rounded-t-[3px]")
                      }
                      style={{ height: percentCss(day.recognised, ceiling) }}
                    />
                  </div>
                );
              })}
            </div>

            {/* recessive baseline + axis labels */}
            <div className="h-px bg-hairline-strong" />
            <div className="mt-2 flex gap-[clamp(0.375rem,1.2vw,1.25rem)]">
              {days.map((day) => (
                <span
                  key={day.date}
                  className="mono min-w-0 flex-1 text-center text-[10px] text-faint"
                >
                  {day.date.slice(5)}
                </span>
              ))}
            </div>
          </div>
        )}
      </section>

      <div className="grid items-start gap-4 lg:[grid-template-columns:minmax(0,1.1fr)_minmax(0,0.9fr)]">
        {/* The table view: every figure the chart encodes, readable without it. */}
        <section className="panel">
          <PanelHead legend="By settlement date" />
          <div className="overflow-x-auto">
            <table className="grid-table">
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col" className="!text-right">
                    Recognised
                  </th>
                  <th scope="col" className="!text-right">
                    Blocked
                  </th>
                </tr>
              </thead>
              <tbody>
                {days.map((day) => (
                  <tr key={day.date}>
                    <td className="mono text-muted">{day.date}</td>
                    <td className="mono text-right">{formatRupees(day.recognised)}</td>
                    <td
                      className={
                        "mono text-right " + (day.blocked > 0 ? "text-signal" : "text-faint")
                      }
                    >
                      {day.blocked > 0 ? formatRupees(day.blocked) : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <div className="flex flex-col gap-3">
          <Readout
            label="Unrecognised cash"
            value={formatRupees(unrecognised_cash)}
            sub="Credits that never tied to any settlement."
          />
          <div className="panel p-4">
            <p className="text-[11.5px] leading-relaxed text-muted">
              Projected over this corpus&apos;s own T+2 settlement window, not the calendar
              &ldquo;today&rdquo;. &ldquo;Blocked&rdquo; is payout still held behind an open
              exception
              {anyBlocked ? "" : " (none in this window)"}.
            </p>
          </div>
        </div>
      </div>
    </>
  );
}
