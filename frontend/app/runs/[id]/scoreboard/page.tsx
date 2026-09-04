"use client";

import { use } from "react";
import { Readout, StatGroup, StatRow, Ticks } from "@/components/Stat";
import { formatClassName } from "@/lib/difficulty";
import { InfoDot } from "@/components/InfoDot";
import { Impact } from "@/components/Impact";
import { Loading } from "@/components/Surface";
import { runImpact } from "@/lib/impact";
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
  // Precision, recall and false matches mean nothing without an answer key to
  // score against, and an uploaded corpus has none. Rather than print three em
  // dashes and explain them away, the panel is simply not there: a figure that
  // cannot be measured is not a figure this surface should show.
  const scored = m.precision !== null && m.precision !== undefined;
  // Absent for runs completed before the payment counts were stored: there is
  // no honest way back to a payment total from a percentage, so the panel is
  // simply not drawn rather than estimated.
  const impact = runImpact(m);
  // Widest class first: the one carrying most of the corpus is the one whose
  // recall the headline rate is mostly made of.
  const byClass: ClassRow[] = Object.entries(m.by_class ?? {})
    .map(([name, score]) => ({ name, count: score.count, recall: score.recall }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));

  return (
    <>
      {/* What the run was worth, before how right it was. The rates below are
          the engine's report on itself; this is the run read as work done,
          and it is the first thing a finance lead is actually asking. */}
      {impact && <Impact impact={impact} />}

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

      {/* `llm_degraded` is set when *any* triage call failed, not only when
          they all did, so the message must not claim an assist rate of zero
          while the tile above it reads 9.5%. What is always true is the part
          that matters: whatever could not be triaged was filed rather than
          guessed at. */}
      {m.llm_degraded && (
        <p className="rounded-[3px] border border-caution bg-caution-bg px-4 py-3 text-sm text-caution">
          Assisted triage degraded on this run: some calls to the model did not complete.
          {m.assist_rate > 0
            ? " The items that were triaged were re-checked before anything was written, and everything else was filed as a typed exception."
            : " Nothing was triaged, and every item that would have gone to triage was filed as a typed exception instead."}{" "}
          Nothing was guessed at either way, so the open exception count is higher than a clean run
          would produce rather than the match rate being inflated.
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

        <div
          className={
            "grid items-start gap-3 " + (scored ? "lg:grid-cols-3" : "lg:grid-cols-2")
          }
        >
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
              note="In and out, across triage and explanations. An answer served from cache is counted at what it originally cost, so this is what the run's work is worth rather than what today's attempt happened to spend."
            />
            <StatRow
              label="Assisted triage"
              value={m.llm_degraded ? "Degraded" : "Nominal"}
              tone={m.llm_degraded ? "signal" : "positive"}
              note="Degrades to typed exceptions, never to a guess."
            />
          </StatGroup>

          {scored && (
            <StatGroup
              title="Measured accuracy"
              info={
                <InfoDot label="How accuracy is measured">
                  This run used a generated corpus, which ships with a truth file naming every group
                  that genuinely belongs together. The engine never sees it; scoring compares what
                  the run tied against what the answer key says, after the fact.
                  <span className="mt-2 block">
                    <b className="text-foreground">Precision</b> is how much of what was tied was
                    right, <b className="text-foreground">recall</b> how much of what should have
                    tied did, and <b className="text-foreground">false matches</b> counts ties the
                    verifier should never have written.
                  </span>
                  <span className="mt-2 block">
                    An uploaded dataset has no answer key, so this panel is not shown for one:
                    nothing could honestly be scored against it.
                  </span>
                </InfoDot>
              }
            >
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
          )}
        </div>
      </section>

      {byClass.length > 0 && <ClassTable rows={byClass} />}
    </>
  );
}
