"use client";

import { use } from "react";
import { Stat } from "@/components/Stat";
import { formatRupees } from "@/lib/money";
import { useRun } from "@/lib/useRun";

function formatRate(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

export default function ScoreboardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const run = useRun(id);

  if (!run) return <p className="text-sm text-muted">Loading…</p>;
  if (run.state === "failed") {
    return <p className="text-sm text-signal">This run failed: {run.error}</p>;
  }
  if (!run.metrics) {
    return <p className="text-sm text-muted">Metrics will appear once the run finishes scoring.</p>;
  }

  const m = run.metrics;

  return (
    <div className="max-w-2xl">
      <dl>
        <Stat label="Match rate (auto)" value={formatRate(m.auto_rate)} />
        <Stat label="Assist rate" value={formatRate(m.assist_rate)} />
        <Stat label="False matches" value={m.false_matches ?? "—"} />
        <Stat label="Open exceptions" value={m.open_exceptions} />
        <Stat label="Rupees at risk" value={formatRupees(m.amount_at_risk)} />
        <Stat label="Throughput" value={`${m.throughput_rps.toFixed(1)} rec/s`} />
        <Stat label="LLM requests used" value={m.llm_requests} />
        <Stat label="LLM degraded" value={m.llm_degraded ? "Yes" : "No"} mono={false} />
        {m.precision !== null && m.precision !== undefined && (
          <Stat label="Precision" value={formatRate(m.precision)} />
        )}
        {m.recall !== null && m.recall !== undefined && <Stat label="Recall" value={formatRate(m.recall)} />}
        <Stat label="Output hash" value={m.output_hash} />
      </dl>

      <p className="mt-4 text-xs text-muted">
        Determinism check: re-running this exact seed and size produces the same output hash above.
      </p>

      <a
        href={`/api/runs/${id}/export.csv`}
        className="mt-6 inline-block border border-hairline px-3 py-2 text-sm hover:border-foreground"
      >
        Export exceptions CSV
      </a>
    </div>
  );
}
