"use client";

import { use } from "react";
import { Readout, StatGroup, StatRow, Ticks } from "@/components/Stat";
import { Loading } from "@/components/Surface";
import { formatRupees } from "@/lib/money";
import { percentCss, ticksForRate } from "@/lib/scale";
import { useRun } from "@/lib/useRun";

function formatRate(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

function formatOptionalRate(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? "—" : formatRate(rate);
}

/** Milliseconds as the unit a reader of this figure actually thinks in. */
function formatDuration(ms: number): string {
  return ms < 1000 ? `${ms.toFixed(0)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

/**
 * `fee_gst_delta` is how the generator names a class; this is how a person in
 * finance reads it. GST and UTR are the two words in these names that are
 * acronyms rather than words, and lowercasing them reads as a typo.
 */
const ACRONYMS: Record<string, string> = { gst: "GST", utr: "UTR" };

function formatClassName(name: string): string {
  const words = name
    .split("_")
    .map((word) => ACRONYMS[word] ?? word)
    .join(" ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

type ClassRow = { name: string; count: number; recall: number | null | undefined };

/**
 * Recall for every difficulty class the generator planted, scored against the
 * truth file. This is the answer to "one cherry-picked match proves nothing":
 * the corpus is seeded with named corruptions -- fee and GST deltas, refunds
 * inside a batch, chargebacks, splits, duplicates, a narration with no UTR --
 * and each one is listed here with what the run actually did to it, including
 * the classes it got wrong.
 */
function ClassTable({ rows }: { rows: ClassRow[] }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <h3 className="legend legend-hi">Recall by difficulty class</h3>
        <span className="mono ml-auto text-[12px] text-faint">
          {rows.length} CLASSES SEEDED
        </span>
      </header>

      <div className="overflow-x-auto">
        <table className="grid-table">
          <thead>
            <tr>
              <th scope="col">Class</th>
              <th scope="col">Records</th>
              <th scope="col" className="w-[45%]">
                Recall
              </th>
              <th scope="col" className="text-right">
                Scored
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name}>
                <td className="font-medium whitespace-nowrap">{formatClassName(row.name)}</td>
                <td className="tabular text-muted">{row.count.toLocaleString()}</td>
                <td>
                  <div className="meter" aria-hidden>
                    <span
                      className="meter-fill"
                      style={{ width: percentCss(row.recall ?? 0, 1) }}
                    />
                  </div>
                </td>
                <td
                  className={
                    "mono tabular text-right " + (row.recall === 0 ? "text-signal" : "text-muted")
                  }
                >
                  {formatOptionalRate(row.recall)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="border-t border-hairline px-3 py-2.5 text-[12.5px] leading-relaxed text-faint">
        Counts are the records the generator planted in each class, not a sample size chosen for
        statistics: on a small corpus several classes carry a single record, so those read 0% or
        100% and nothing in between. Raise the dataset size to give each class more to be scored on.
      </p>
    </section>
  );
}

/**
 * What the scoreboard looks like before scoring has run. The four readouts
 * are drawn in their final places with an em dash in each, so the surface
 * keeps its shape while the run works and no figure has to move once the
 * real ones land. An em dash rather than a zero, for the same reason
 * precision reads as one on a corpus with no truth file: nothing has been
 * measured yet, and a zero would claim otherwise.
 */
const PENDING_FIELDS = [
  { label: "Match rate (auto)", sub: "Tied by a deterministic pass, with no assist." },
  { label: "Assist rate", sub: "Triaged, then re-checked before anything was written." },
  { label: "Open exceptions", sub: "Counted once every pass has had its turn." },
  { label: "Rupees at risk", sub: "Held behind whatever does not tie out." },
];

function PendingScoreboard() {
  return (
    <>
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(230px,1fr))]">
        {PENDING_FIELDS.map((field) => (
          <div key={field.label} className="readout">
            <span className="legend">{field.label}</span>
            <p className="readout-val text-hairline-strong">—</p>
            <Ticks on={0} />
            <p className="readout-sub text-faint">{field.sub}</p>
          </div>
        ))}
      </div>
      <p className="text-[13px] leading-relaxed text-faint">
        Every figure here is computed by the run itself and written once, at the scoring stage. The
        board stays empty until then rather than filling in as it goes, so nothing on it is ever a
        partial count.
      </p>
    </>
  );
}

export default function ScoreboardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const run = useRun(id);

  if (!run) return <Loading />;
  if (run.state === "failed") {
    return <p className="text-sm text-signal">This run failed: {run.error}</p>;
  }
  if (!run.metrics) return <PendingScoreboard />;

  const m = run.metrics;
  // Precision, recall and false matches are only scored where a truth file
  // exists; on a corpus without one they read as an em dash, and the note
  // under the field says why rather than leaving a blank card unexplained.
  const scored = m.precision !== null && m.precision !== undefined;
  // Widest class first: the one carrying most of the corpus is the one whose
  // recall the headline rate is mostly made of.
  const byClass: ClassRow[] = Object.entries(m.by_class ?? {})
    .map(([name, score]) => ({ name, count: score.count, recall: score.recall }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));

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
          sub={`${m.open_rate === 0 ? "None" : formatRate(m.open_rate)} of the batch's payments did not tie out.`}
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

      {/* Everything behind the headline, on three plates: what the run read
          and how fast, what the agent spent to do it, and how right it was.
          Grouped rather than spread across one field of equal cards, so a
          figure sits next to the ones that answer the same question. */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <span className="legend legend-hi">Run detail</span>
          <span aria-hidden className="h-px flex-1 bg-hairline" />
          <span className="mono text-[12px] text-faint">VERIFIER-GATED</span>
        </div>

        <div className="grid items-start gap-3 lg:grid-cols-3">
          <StatGroup title="Volume and speed">
            <StatRow
              label="Records"
              value={m.records.toLocaleString()}
              note="Invoices, payments, settlements and bank lines read from the dataset."
            />
            <StatRow
              label="Throughput"
              value={`${m.throughput_rps.toFixed(1)} rec/s`}
              note="Sustained across the whole run, not a peak."
            />
            <StatRow
              label="Wall clock"
              value={formatDuration(m.p50_ms)}
              note="Matching, triage and scoring end to end, model calls included."
            />
          </StatGroup>

          <StatGroup title="What the agent spent">
            <StatRow
              label="Model requests"
              value={m.llm_requests.toLocaleString()}
              note="Calls issued to the model this run."
            />
            <StatRow
              label="Tokens"
              value={m.llm_tokens.toLocaleString()}
              note="In and out, across triage and explanations."
            />
            <StatRow
              label="Assisted triage"
              value={m.llm_degraded ? "Degraded" : "Nominal"}
              tone={m.llm_degraded ? "signal" : "positive"}
              note="Degrades to typed exceptions, never to a guess."
            />
          </StatGroup>

          <StatGroup title="Measured accuracy">
            <StatRow
              label="Precision"
              value={formatOptionalRate(m.precision)}
              note="Of what was tied, how much was right."
            />
            <StatRow
              label="Recall"
              value={formatOptionalRate(m.recall)}
              note="Of what should have tied, how much did."
            />
            <StatRow
              label="False matches"
              value={m.false_matches ?? "—"}
              tone={m.false_matches === 0 ? "positive" : m.false_matches ? "signal" : undefined}
              note="Ties the check should not have written."
            />
          </StatGroup>
        </div>

        {!scored && (
          <p className="text-[12.5px] leading-relaxed text-faint">
            Precision, recall and false matches are scored against a truth file. This corpus carries
            none, so they read as an em dash rather than a zero.
          </p>
        )}
      </section>

      {byClass.length > 0 && <ClassTable rows={byClass} />}
    </>
  );
}
