"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { RunProgress, type RunEvent } from "@/components/RunProgress";
import { StatusBadge } from "@/components/StatusBadge";
import { Stage } from "@/components/Surface";
import { StatusStrip } from "@/components/StatusStrip";
import { completedDuration, formatElapsed, runStartedAt, useNow } from "@/lib/useElapsed";
import { refreshRun, useRun } from "@/lib/useRun";

const TABS = [
  { slug: "scoreboard", label: "Scoreboard" },
  { slug: "chain", label: "Chain" },
  { slug: "exceptions", label: "Exceptions" },
  { slug: "cash", label: "Cash position" },
];

function formatRate(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

export function RunShell({ runId, children }: { runId: string; children: React.ReactNode }) {
  const pathname = usePathname();
  const [log, setLog] = useState<RunEvent[]>([]);
  const run = useRun(runId);

  useEffect(() => {
    // Same-origin EventSource sends the session cookie automatically; the
    // proxy at app/api/[...path]/route.ts forwards it to the backend.
    const source = new EventSource(`/api/runs/${runId}/stream`);
    source.onmessage = (event) => {
      const payload = JSON.parse(event.data) as {
        state: string;
        error?: string | null;
        at?: string | null;
      };
      // The worker's own timestamp where there is one -- including on the
      // transitions replayed to a client that connected after they happened.
      // Arrival time is the fallback, and the only frame available for an
      // event that carries none.
      const serverAt = payload.at ? Date.parse(payload.at) : NaN;
      setLog((prev) => [
        ...prev,
        {
          state: payload.state,
          error: payload.error,
          serverAt: Number.isNaN(serverAt) ? null : serverAt,
          clientAt: Date.now(),
        },
      ]);
      // The stream is the fast path for state; the row behind it carries the
      // metrics and the forecast, so a state change is worth a read now
      // rather than at the poller's next turn.
      refreshRun(runId);
      if (payload.state === "complete" || payload.state === "failed") {
        source.close();
      }
    };
    source.onerror = () => source.close();
    return () => source.close();
  }, [runId]);

  const latest = log[log.length - 1] ?? null;
  const state = latest?.state ?? run?.state ?? null;
  const isTerminal = state === "complete" || state === "failed";
  const metrics = run?.metrics ?? null;

  // The clock is anchored to when the API accepted the run, so it survives a
  // reload and counts the time the job spent queued -- and falls back to this
  // mount if the browser's own clock makes that reading impossible.
  const now = useNow(!isTerminal);
  const [mountedAt] = useState(() => Date.now());
  const startedAt = runStartedAt(run?.created_at, now, mountedAt);
  // The same instant in the server's frame, for differencing against the
  // worker's transition timestamps.
  const serverStart = run?.created_at ? Date.parse(run.created_at) : NaN;
  // Once a run is over, its duration is the distance between two of the
  // server's own timestamps. Measuring a finished run against the browser
  // clock would keep counting long after it stopped.
  const finalDuration = completedDuration(run?.created_at, run?.updated_at);
  const elapsed =
    isTerminal && finalDuration !== null ? finalDuration : Math.max(0, now - startedAt);

  // Every value here is read straight off the API's metrics object; the
  // strip formats, it never derives.
  const strip: { label: string; value?: React.ReactNode; tone?: string }[] = [
    {
      label: state ? state.toUpperCase() : "CONNECTING",
      tone:
        state === "failed"
          ? "var(--signal)"
          : state === "complete"
            ? "var(--positive)"
            : "var(--readout-hi)",
    },
  ];
  if (!isTerminal) {
    strip.push({ label: "ELAPSED", value: formatElapsed(elapsed) });
  } else if (finalDuration !== null) {
    strip.push({ label: "TOOK", value: formatElapsed(finalDuration) });
  }
  if (metrics) {
    strip.push({ label: "ASSIST", value: formatRate(metrics.assist_rate) });
    // Only where an answer key exists to score against. An uploaded corpus has
    // none, and a dash on the instrument's bottom line reads as a broken gauge
    // rather than as "not applicable".
    if (metrics.false_matches !== null && metrics.false_matches !== undefined) {
      strip.push({ label: "FALSE MATCHES", value: String(metrics.false_matches) });
    }
    strip.push({ label: "THROUGHPUT", value: `${metrics.throughput_rps.toFixed(1)} rec/s` });
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* context bar */}
      <div className="flex min-h-[54px] shrink-0 flex-wrap items-center gap-x-3.5 gap-y-1 border-b border-hard bg-surface px-[clamp(0.875rem,1.6vw,1.625rem)] py-2">
        <div className="flex min-w-0 items-center gap-2.5">
          <Link href="/run" className="legend hover:text-foreground">
            Run
          </Link>
          <span aria-hidden className="text-faint">
            /
          </span>
          <span className="mono text-[14.5px] font-medium">{runId.slice(0, 8)}</span>
          {state && <StatusBadge state={state} />}
          {/* Carried on every surface of a sabotaged run, so no figure here is
              ever read as though it came from clean books. */}
          {run?.mutations && run.mutations.length > 0 && (
            <span className="chip border-signal text-signal" title={run.mutations.join(", ")}>
              {run.mutations.length} corruption{run.mutations.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {metrics && (
            <span className="mono hidden text-[12.5px] text-faint sm:inline">
              {metrics.records.toLocaleString()} rec
            </span>
          )}
          {/* Both only once there is a result to export; before that they
              would download a 404. The PDF is the whole run as a document --
              verdict, exceptions, cash position, and what reproduces it --
              for the reader who will never be handed a URL. */}
          {state === "complete" && (
            <a href={`/api/runs/${runId}/report.pdf`} className="btn btn-sm">
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden
              >
                <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
                <path d="M14 3v5h5" />
                <path d="M9 13h6M9 17h4" />
              </svg>
              Report
            </a>
          )}
          <a href={`/api/runs/${runId}/export.csv`} className="btn btn-sm">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M12 3v11m0 0 4-4m-4 4-4-4" />
              <path d="M4 17v3h16v-3" />
            </svg>
            Export CSV
          </a>
        </div>
      </div>

      {/* channel tabs: a machined segmented control, not an underline nav */}
      <nav
        aria-label="Run views"
        className="flex shrink-0 items-stretch overflow-x-auto border-b border-hairline bg-sunk px-[clamp(0.875rem,1.6vw,1.625rem)]"
      >
        {TABS.map((tab, i) => {
          const href = `/runs/${runId}/${tab.slug}`;
          const active = pathname === href;
          const count = tab.slug === "exceptions" ? metrics?.open_exceptions : undefined;
          return (
            <Link
              key={tab.slug}
              href={href}
              aria-current={active ? "page" : undefined}
              className={
                "relative flex min-h-[42px] items-center gap-2 whitespace-nowrap border-r border-hairline px-4 text-[12.5px] font-semibold uppercase tracking-[0.1em] transition-colors " +
                (i === 0 ? "border-l " : "") +
                (active ? "bg-surface text-foreground" : "text-muted hover:text-foreground")
              }
            >
              {active && (
                <span aria-hidden className="absolute inset-x-0 -top-px h-0.5 bg-readout-hi" />
              )}
              {tab.label}
              {count !== undefined && count !== null && count > 0 && (
                <span className="mono rounded-sm bg-signal-bg px-1.5 py-px text-[11.5px] tracking-normal text-signal">
                  {count}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      <Stage>
        {/* While a run is in flight the pipeline itself is the surface: which
            stage is live, what it is doing, and how long it has been going. */}
        {(!isTerminal || state === "failed") && (
          <RunProgress
            events={log}
            state={state}
            elapsed={elapsed}
            serverStart={Number.isNaN(serverStart) ? null : serverStart}
            clientStart={startedAt}
            failed={state === "failed"}
          />
        )}

        {state === "failed" && (
          <p
            role="alert"
            className="rounded-[3px] border border-signal bg-signal-bg px-4 py-3 text-sm text-signal"
          >
            Run failed: {latest?.error ?? run?.error ?? "unknown error"}
          </p>
        )}

        {children}
      </Stage>

      <StatusStrip segments={strip} />
    </div>
  );
}
