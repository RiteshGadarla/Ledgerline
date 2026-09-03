"use client";

import { formatDuration, formatElapsed } from "@/lib/useElapsed";

/**
 * The processing screen.
 *
 * A reconciliation run is not a spinner's worth of waiting: it walks a fixed
 * six-stage pipeline (workers/tasks.py), and every stage is something a
 * finance lead would recognise as work. So the wait is drawn as the pipeline
 * itself -- which stage is live, what that stage is actually doing, how long
 * each one took, and how long the whole thing has been going.
 *
 * Every figure on it is expressed as an offset into the run, so they all
 * agree with each other and with the clock. The stream stamps each
 * transition with the worker's own time, and the run row carries the moment
 * the API accepted it, so a stage's start is the difference between two of
 * the server's timestamps -- no browser clock in it at all, and no drift.
 * Only the live stage is measured against the running total, which is the
 * one figure that has to come from the browser to keep ticking.
 */
export type RunEvent = {
  state: string;
  error?: string | null;
  /** The worker's timestamp for this transition, parsed. Null for an event
   *  that arrived without one -- a trace that expired, an older run. */
  serverAt: number | null;
  /** When this console saw it, as the fallback for exactly that case. */
  clientAt: number;
};

const STAGES = [
  {
    key: "queued",
    label: "Queued",
    note: "Waiting for a worker to pick the job up.",
  },
  {
    key: "normalising",
    label: "Normalising",
    note: "Reading every file and canonicalising the rows into one shape.",
  },
  {
    key: "matching",
    label: "Matching",
    note: "Deterministic passes: exact reference, then UTR, then amount and date.",
  },
  {
    key: "triaging",
    label: "Triaging",
    note: "Assisted triage on whatever did not tie. Every suggestion is verifier-gated.",
  },
  {
    key: "explaining",
    label: "Explaining",
    note: "Writing a plain-English reason for each exception still open.",
  },
  {
    key: "scoring",
    label: "Scoring",
    note: "Scoring against the answer key and sealing the output hash.",
  },
] as const;

type StageStatus = "done" | "live" | "failed" | "pending";

/**
 * The stage the run is in, as an index into STAGES. A state we don't know
 * (a stage added to the worker before this list caught up) reads as "still
 * going" rather than silently rewinding the rail to the start.
 */
function stageIndex(state: string | null, previous: number): number {
  if (state === null) return previous;
  if (state === "complete") return STAGES.length;
  // A failure does not advance the rail: the run stopped in whatever stage
  // it was already in, and that is the stage worth pointing at.
  if (state === "failed") return previous;
  const found = STAGES.findIndex((stage) => stage.key === state);
  return found === -1 ? previous : found;
}

export function RunProgress({
  events,
  state,
  elapsed,
  serverStart,
  clientStart,
  failed,
}: {
  events: RunEvent[];
  /** The run's current state, from the stream if it is up and the polled row if not. */
  state: string | null;
  /** Milliseconds since the API accepted the run. Ticks; frozen on terminal. */
  elapsed: number;
  /** When the API accepted the run, in the server's frame (from created_at)
   *  and in the browser's. Every stage offset is measured from one or the
   *  other, matching whichever frame the event itself came in. */
  serverStart: number | null;
  clientStart: number;
  failed: boolean;
}) {
  /** How far into the run a transition happened. Server timestamps are
   *  differenced against the server's own start, browser ones against the
   *  browser's, so neither frame is ever compared with the other. */
  function offsetOf(event: RunEvent): number {
    if (event.serverAt !== null && serverStart !== null) return event.serverAt - serverStart;
    return event.clientAt - clientStart;
  }

  // First sighting of each state. Re-entering a state (which the pipeline
  // never does) would keep the first, so a duration is never negative.
  const firstSeen = new Map<string, number>();
  for (const event of events) {
    if (!firstSeen.has(event.state)) firstSeen.set(event.state, offsetOf(event));
  }
  let current = 0;
  for (const event of events) current = stageIndex(event.state, current);
  current = stageIndex(state, current);

  // The queue is the one stage nothing announces: a run is queued from the
  // moment the API accepts it, which is offset zero by definition. Seeding
  // that turns the first row from a permanent em dash into the wait for a
  // worker -- the figure worth having when a run seems slow to start.
  //
  // Only seeded when the wait is actually knowable: when normalising's start
  // bounds it, or when the run is still in the queue and it is the elapsed
  // total. Joining a run mid-flight with no trace to replay leaves it an em
  // dash, rather than crediting the queue with time the pipeline spent.
  if (!firstSeen.has("queued") && (firstSeen.has("normalising") || current === 0)) {
    firstSeen.set("queued", 0);
  }

  const active = STAGES[Math.min(current, STAGES.length - 1)];

  /** How long a stage ran: until the next stage started, or -- for the one
   *  still running -- however much of the elapsed total is left over after
   *  the stages before it. Unknown only when we never learned when it began,
   *  which is drawn as an em dash and never as a zero. */
  function durationOf(index: number): number | null {
    const startedAt = firstSeen.get(STAGES[index].key);
    if (startedAt === undefined) return null;
    for (let next = index + 1; next < STAGES.length; next += 1) {
      const nextStart = firstSeen.get(STAGES[next].key);
      if (nextStart !== undefined) return nextStart - startedAt;
    }
    const terminal = firstSeen.get("complete") ?? firstSeen.get("failed");
    if (terminal !== undefined) return terminal - startedAt;
    return index === current ? Math.max(0, elapsed - startedAt) : null;
  }

  function statusOf(index: number): StageStatus {
    if (index < current) return "done";
    if (index === current) return failed ? "failed" : "live";
    return "pending";
  }

  return (
    <section className="panel border-readout-hi">
      <div className="panel-head border-b-hairline">
        <span className="legend legend-hi">Closing the books</span>
        <span className="mono ml-auto text-[12px] text-faint">
          STAGE {Math.min(current + 1, STAGES.length)} OF {STAGES.length}
        </span>
      </div>

      {/* The headline: what is happening, and for how long. The clock is set
          at readout scale because it is the figure the operator is actually
          watching while there are no metrics to read yet. */}
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4 px-4 pt-4">
        <div className="min-w-0 flex-1">
          <span className="legend">{failed ? "Stopped at" : "Now"}</span>
          <p className="mt-2 flex items-center gap-2.5 text-[16.5px] font-medium tracking-[-0.01em]">
            {!failed && (
              <span aria-hidden className="pulse-dot h-1.5 w-1.5 rounded-full bg-readout-hi" />
            )}
            {active.label}
          </p>
          <p className="mt-1.5 max-w-prose text-[13.5px] leading-relaxed text-muted">{active.note}</p>
        </div>

        <div className="text-right">
          <span className="legend">Elapsed</span>
          <p
            className="mono mt-2 text-[clamp(1.5rem,3vw,2.125rem)] font-medium leading-none tracking-[-0.025em]"
            // Announced once at the end rather than ten times a second.
            aria-live="off"
          >
            {formatElapsed(elapsed)}
          </p>
        </div>
      </div>

      {/* One lit segment per finished stage, a sweep on the live one. */}
      <div className="flex gap-1 px-4 pt-4" aria-hidden>
        {STAGES.map((stage, index) => {
          const status = statusOf(index);
          return (
            <span
              key={stage.key}
              className={
                "stage-seg " +
                (status === "done"
                  ? "stage-seg-done"
                  : status === "live"
                    ? "stage-seg-live"
                    : status === "failed"
                      ? "stage-seg-failed"
                      : "")
              }
            />
          );
        })}
      </div>

      <ol aria-live="polite" className="flex flex-col p-4 pt-3.5">
        {STAGES.map((stage, index) => {
          const status = statusOf(index);
          const duration = durationOf(index);
          return (
            <li
              key={stage.key}
              className="flex items-baseline gap-2.5 border-b border-hairline py-2 last:border-b-0"
            >
              <span
                aria-hidden
                className={
                  "mono w-3 shrink-0 text-[12.5px] " +
                  (status === "live"
                    ? "text-accent"
                    : status === "failed"
                      ? "text-signal"
                      : status === "done"
                        ? "text-readout-hi"
                        : "text-hairline-strong")
                }
              >
                {status === "live" ? "▸" : status === "failed" ? "×" : status === "done" ? "•" : "·"}
              </span>

              <span
                className={
                  "shrink-0 text-[14px] " +
                  (status === "pending"
                    ? "text-faint"
                    : status === "failed"
                      ? "font-medium text-signal"
                      : status === "live"
                        ? "font-medium text-foreground"
                        : "text-muted")
                }
              >
                {stage.label}
              </span>

              <span className="min-w-0 flex-1 truncate text-[13px] text-faint">{stage.note}</span>

              <span
                className={
                  "mono shrink-0 text-[13px] " +
                  (status === "pending" ? "text-hairline-strong" : "text-muted")
                }
              >
                {duration === null ? "—" : formatDuration(duration)}
              </span>
            </li>
          );
        })}
      </ol>

      {/* The raw stream underneath, unchanged in substance: this is still the
          transcript the run published, and it is what you read when a stage
          takes longer than it should. */}
      {events.length > 0 && (
        <div className="border-t border-hairline bg-sunk px-4 py-3">
          <span className="legend">Trace</span>
          <ol className="mono mt-2 flex flex-col gap-1 text-[12.5px]">
            {events.map((event, index) => (
              <li
                key={index}
                className={
                  "log-line flex items-baseline gap-2.5 " +
                  (index === events.length - 1 ? "text-muted" : "text-faint")
                }
              >
                <span className="tabular-nums text-hairline-strong">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span>
                  {event.state}
                  {event.error ? `: ${event.error}` : ""}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
