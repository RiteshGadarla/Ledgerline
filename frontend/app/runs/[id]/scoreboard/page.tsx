"use client";

import { use } from "react";
import { Kpi, Readout } from "@/components/Stat";
import { Loading } from "@/components/Surface";
import { formatRupees } from "@/lib/money";
import { ticksForRate } from "@/lib/scale";
import { useRun } from "@/lib/useRun";

function formatRate(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

function formatOptionalRate(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? "-" : formatRate(rate);
}

export default function ScoreboardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const run = useRun(id);

  if (!run) return <Loading />;
  if (run.state === "failed") {
    return <p className="text-sm text-signal">This run failed: {run.error}</p>;
  }
  if (!run.metrics) {
    return <p className="text-sm text-muted">Metrics will appear once the run finishes scoring.</p>;
  }

  const m = run.metrics;
  // Precision, recall and false matches are only scored where a truth file
  // exists; on a corpus without one they read as an em dash, and the note
  // under the field says why rather than leaving a blank card unexplained.
  const scored = m.precision !== null && m.precision !== undefined;

  return (
    <>
      {/* The four figures a finance lead reads first. The tick scale under
          each is lit from the same rate the number reports. */}
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(230px,1fr))]">
        <Readout
          label="Match rate (auto)"
          value={formatRate(m.auto_rate)}
          ticks={{ on: ticksForRate(m.auto_rate) }}
          sub="Tied by a deterministic pass, with no assist."
        />
        <Readout
          label="Assist rate"
          value={formatRate(m.assist_rate)}
          ticks={{ on: ticksForRate(m.assist_rate) }}
          sub="Triaged, then re-checked before anything was written."
        />
        <Readout
          label="Open exceptions"
          value={String(m.open_exceptions)}
          ticks={{
            on: 40 - ticksForRate(m.open_rate),
            risk: ticksForRate(m.open_rate),
          }}
          sub={`${m.open_rate === 0 ? "None" : formatRate(m.open_rate)} of ${m.records.toLocaleString()} records did not tie out.`}
        />
        <Readout
          label="Rupees at risk"
          value={formatRupees(m.amount_at_risk)}
          tone={m.open_exceptions > 0 ? "signal" : undefined}
          sub="Held behind the open exceptions."
        />
      </div>

      {m.llm_degraded && (
        <p className="rounded-[3px] border border-caution bg-caution-bg px-4 py-3 text-sm text-caution">
          Assisted triage degraded on this run. Assist rate reports zero and every item that would
          have gone to triage is filed as a typed exception instead.
        </p>
      )}

      {/* Everything behind the headline: volume, speed, what the agent spent,
          and the accuracy scores. One field of equal cards, no figure here
          outranks another, so none is framed as though it does. */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <span className="legend legend-hi">Run detail</span>
          <span aria-hidden className="h-px flex-1 bg-hairline" />
          <span className="mono text-[10.5px] text-faint">VERIFIER-GATED</span>
        </div>

        <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(178px,1fr))]">
          <Kpi
            label="Records"
            value={m.records.toLocaleString()}
            note="Rows read from the dataset."
          />
          <Kpi
            label="Throughput"
            value={`${m.throughput_rps.toFixed(1)} rec/s`}
            note="Sustained across the whole run."
          />
          <Kpi
            label="Latency p50 / p95"
            value={`${m.p50_ms.toFixed(0)} / ${m.p95_ms.toFixed(0)} ms`}
            note="Median and tail, per record."
          />
          <Kpi label="LLM requests" value={m.llm_requests} note="Model calls this run spent." />
          <Kpi
            label="Assisted triage"
            value={m.llm_degraded ? "Degraded" : "Nominal"}
            tone={m.llm_degraded ? "signal" : "positive"}
            note="Degrades to typed exceptions, never to a guess."
          />
          <Kpi
            label="False matches"
            value={m.false_matches ?? "-"}
            tone={m.false_matches === 0 ? "positive" : m.false_matches ? "signal" : undefined}
            note="Ties the check should not have written."
          />
          <Kpi
            label="Precision"
            value={formatOptionalRate(m.precision)}
            note="Of what was tied, how much was right."
          />
          <Kpi
            label="Recall"
            value={formatOptionalRate(m.recall)}
            note="Of what should have tied, how much did."
          />
        </div>

        {!scored && (
          <p className="text-[11px] leading-relaxed text-faint">
            Precision, recall and false matches are scored against a truth file. This corpus carries
            none, so they read as an em dash rather than a zero.
          </p>
        )}
      </section>
    </>
  );
}
