"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { Stage } from "@/components/Surface";
import { StatusStrip } from "@/components/StatusStrip";
import { useRun } from "@/lib/useRun";

type StreamEvent = { state: string; error?: string | null };

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
  const [log, setLog] = useState<StreamEvent[]>([]);
  const run = useRun(runId);

  useEffect(() => {
    // Same-origin EventSource sends the session cookie automatically; the
    // proxy at app/api/[...path]/route.ts forwards it to the backend.
    const source = new EventSource(`/api/runs/${runId}/stream`);
    source.onmessage = (event) => {
      const payload = JSON.parse(event.data) as StreamEvent;
      setLog((prev) => [...prev, payload]);
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
  if (metrics) {
    strip.push(
      { label: "ASSIST", value: formatRate(metrics.assist_rate) },
      { label: "FALSE MATCHES", value: String(metrics.false_matches ?? "-") },
      { label: "THROUGHPUT", value: `${metrics.throughput_rps.toFixed(1)} rec/s` },
    );
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
          <span className="mono text-[13px] font-medium">{runId.slice(0, 8)}</span>
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
            <span className="mono hidden text-[11px] text-faint sm:inline">
              {metrics.records.toLocaleString()} rec
            </span>
          )}
          <a href={`/api/runs/${runId}/export.csv`} className="btn btn-sm">
            <svg
              width="14"
              height="14"
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
                "relative flex min-h-[42px] items-center gap-2 whitespace-nowrap border-r border-hairline px-4 text-[11px] font-semibold uppercase tracking-[0.1em] transition-colors " +
                (i === 0 ? "border-l " : "") +
                (active ? "bg-surface text-foreground" : "text-muted hover:text-foreground")
              }
            >
              {active && (
                <span aria-hidden className="absolute inset-x-0 -top-px h-0.5 bg-readout-hi" />
              )}
              {tab.label}
              {count !== undefined && count !== null && count > 0 && (
                <span className="mono rounded-sm bg-signal-bg px-1.5 py-px text-[10px] tracking-normal text-signal">
                  {count}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      <Stage>
        {/* The live stream is the surface while a run is in flight. */}
        {!isTerminal && log.length > 0 && (
          <section className="panel border-readout-hi">
            <div className="panel-head border-b-hairline">
              <span className="legend legend-hi">Live</span>
              <span className="chip chip-live ml-auto">
                <span aria-hidden className="dot pulse-dot" />
                {state}
              </span>
            </div>
            <ol aria-live="polite" className="mono flex flex-col gap-1 p-4 text-xs">
              {log.map((entry, index) => {
                const current = index === log.length - 1;
                return (
                  <li
                    key={index}
                    className={
                      "log-line flex items-center gap-2.5 " +
                      (current ? "text-foreground" : "text-faint")
                    }
                  >
                    <span aria-hidden className={current ? "text-accent" : "text-hairline-strong"}>
                      {current ? "▸" : "·"}
                    </span>
                    {entry.state}
                    {entry.error ? `: ${entry.error}` : ""}
                  </li>
                );
              })}
            </ol>
          </section>
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
