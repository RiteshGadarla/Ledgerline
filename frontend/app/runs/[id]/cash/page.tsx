"use client";

import { use } from "react";
import { formatRupees } from "@/lib/money";
import { useRun } from "@/lib/useRun";

export default function CashPositionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const run = useRun(id);

  if (run?.state === "failed") return <p className="text-sm text-signal">This run failed: {run.error}</p>;
  if (!run) return <p className="text-sm text-muted">Loading…</p>;
  if (!run.forecast) return <p className="text-sm text-muted">The cash position will appear once the run completes.</p>;

  const { days, unrecognised_cash } = run.forecast;

  return (
    <div className="max-w-2xl">
      <p className="text-sm text-muted">
        Projected over this corpus&apos;s own T+2 settlement window (not the calendar &ldquo;today&rdquo;).
        &ldquo;Blocked&rdquo; is payout still stuck behind an open exception.
      </p>

      {days.length === 0 ? (
        <p className="mt-4 text-sm text-muted">No settlements in this run to project.</p>
      ) : (
        <table className="mt-4 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-hairline text-left text-muted">
              <th scope="col" className="py-2 font-normal">Date</th>
              <th scope="col" className="py-2 text-right font-normal">Recognised</th>
              <th scope="col" className="py-2 text-right font-normal">Blocked</th>
            </tr>
          </thead>
          <tbody>
            {days.map((day) => (
              <tr key={day.date} className="border-b border-hairline">
                <td className="py-2 font-mono tabular">{day.date}</td>
                <td className="py-2 text-right font-mono tabular">{formatRupees(day.recognised)}</td>
                <td className={`py-2 text-right font-mono tabular ${day.blocked > 0 ? "text-signal" : ""}`}>
                  {formatRupees(day.blocked)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="mt-6 flex items-baseline justify-between border-t border-hairline pt-3 text-sm">
        <span className="text-muted">Unrecognised cash (never tied to any settlement)</span>
        <span className="font-mono tabular">{formatRupees(unrecognised_cash)}</span>
      </div>
    </div>
  );
}
