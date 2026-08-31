"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";

type StreamEvent = { state: string; error?: string | null };

const TABS = [
  { slug: "scoreboard", label: "Scoreboard" },
  { slug: "chain", label: "Chain" },
  { slug: "exceptions", label: "Exceptions" },
  { slug: "cash", label: "Cash position" },
];

export function RunShell({ runId, children }: { runId: string; children: React.ReactNode }) {
  const pathname = usePathname();
  const [log, setLog] = useState<StreamEvent[]>([]);

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
  const isTerminal = latest ? latest.state === "complete" || latest.state === "failed" : false;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-sm tabular">{runId.slice(0, 8)}</span>
        {latest && <StatusBadge state={latest.state} />}
      </div>

      {!isTerminal && log.length > 0 && (
        <ol aria-live="polite" className="border border-hairline p-3 font-mono text-xs">
          {log.map((entry, index) => (
            <li key={index} className="log-line">
              {entry.state}
              {entry.error ? `: ${entry.error}` : ""}
            </li>
          ))}
        </ol>
      )}

      {latest?.state === "failed" && (
        <p role="alert" className="border border-signal bg-signal-bg px-3 py-2 text-sm text-signal">
          Run failed: {latest.error ?? "unknown error"}
        </p>
      )}

      <nav aria-label="Run views" className="flex gap-5 border-b border-hairline text-sm">
        {TABS.map((tab) => {
          const href = `/runs/${runId}/${tab.slug}`;
          const active = pathname === href;
          return (
            <Link
              key={tab.slug}
              href={href}
              aria-current={active ? "page" : undefined}
              className={
                active
                  ? "border-b-2 border-foreground pb-2 font-medium text-foreground"
                  : "border-b-2 border-transparent pb-2 text-muted hover:text-foreground"
              }
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>

      {children}
    </div>
  );
}
