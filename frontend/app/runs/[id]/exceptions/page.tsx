"use client";

import { use, useEffect, useMemo, useState } from "react";
import { Loading } from "@/components/Surface";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/client";
import { formatRupees } from "@/lib/money";
import { formatTimestamp } from "@/lib/time";
import { useRun, useRunResult } from "@/lib/useRun";

type Exception = components["schemas"]["Exception_"];
type DecisionOut = components["schemas"]["DecisionOut"];

export default function ExceptionsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const run = useRun(id);
  const result = useRunResult(id, run?.state === "complete");
  const [decisions, setDecisions] = useState<Record<string, DecisionOut>>({});

  useEffect(() => {
    if (run?.state !== "complete") return;
    api.GET("/runs/{run_id}/decisions", { params: { path: { run_id: id } } }).then(({ data }) => {
      if (!data) return;
      setDecisions(Object.fromEntries(data.map((d) => [d.exception_id, d])));
    });
  }, [id, run?.state]);

  const sorted = useMemo(() => {
    if (!result) return [];
    // Comparing two already-known amounts to order rows is not the kind of
    // derived-number computation the amount-arithmetic rule is about.
    return [...result.exceptions].sort((a, b) => (a.amount_at_risk < b.amount_at_risk ? 1 : -1));
  }, [result]);

  async function decide(exceptionId: string, decision: "approved" | "rejected") {
    const { data } = await api.POST("/runs/{run_id}/exceptions/{exception_id}/decision", {
      params: { path: { run_id: id, exception_id: exceptionId } },
      body: { decision },
    });
    if (data) setDecisions((prev) => ({ ...prev, [exceptionId]: data }));
  }

  if (run?.state === "failed") return <p className="text-sm text-signal">This run failed: {run.error}</p>;
  if (!result) return <Loading />;

  if (sorted.length === 0) {
    return (
      <div className="panel flex flex-col items-center gap-2 px-6 py-14 text-center">
        <span aria-hidden className="chip chip-tied">
          <span className="dot" />
          Tied out
        </span>
        <p className="mt-1 text-sm font-medium">No open exceptions</p>
        <p className="text-xs text-muted">Everything in this run tied out.</p>
      </div>
    );
  }

  const decidedCount = Object.keys(decisions).length;

  return (
    <>
      <div className="flex items-center gap-3">
        <span className="legend">Ordered by rupees at risk</span>
        <span aria-hidden className="h-px flex-1 bg-hairline" />
        <span className="mono text-[12.5px] text-faint">
          {sorted.length} OPEN{decidedCount > 0 ? ` · ${decidedCount} DECIDED` : ""}
        </span>
      </div>

      <ul className="flex flex-col gap-2">
        {sorted.map((exc) => (
          <ExceptionCard key={exc.id} exception={exc} decision={decisions[exc.id]} onDecide={decide} />
        ))}
      </ul>
    </>
  );
}

function ExceptionCard({
  exception,
  decision,
  onDecide,
}: {
  exception: Exception;
  decision: DecisionOut | undefined;
  onDecide: (id: string, decision: "approved" | "rejected") => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <li className="panel overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center gap-3.5 px-3.5 py-3 text-left transition-colors hover:bg-lift-1"
      >
        <span className="chip chip-risk">{exception.code}</span>
        <span className="min-w-0 flex-1 text-[14.5px] leading-snug">
          {exception.explanation ?? exception.suggested_action}
        </span>
        {decision && (
          <span className={"chip " + (decision.decision === "approved" ? "chip-tied" : "")}>
            {decision.decision}
          </span>
        )}
        <span className="mono text-[14.5px] text-signal">{formatRupees(exception.amount_at_risk)}</span>
        <span aria-hidden className="mono w-3 text-[12.5px] text-faint">
          {open ? "▾" : "▸"}
        </span>
      </button>

      {open && (
        <div className="border-t border-hairline bg-sunk px-3.5 py-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Detail label="Records">
              <span className="mono text-[13px] leading-relaxed">
                {exception.records.map((r) => `${r.kind}:${r.id}`).join("\n")}
              </span>
            </Detail>
            <Detail label="Passes attempted">
              <span className="mono text-[13px] leading-relaxed">
                {exception.attempted.join("\n")}
              </span>
            </Detail>
            {exception.rejected_proposal && (
              <Detail label={`Rejected proposal · ${exception.rejected_proposal.proposed_by}`}>
                <span className="mono text-[13px] leading-relaxed text-signal">
                  failed check{"\n"}
                  {exception.rejected_proposal.failed_check}
                </span>
              </Detail>
            )}
          </div>

          <hr className="rule my-3.5" />

          <div className="flex flex-wrap items-center gap-2.5">
            <span className="min-w-0 flex-1 text-[13.5px] text-muted">
              {exception.suggested_action}
            </span>
            <button
              type="button"
              onClick={() => onDecide(exception.id, "rejected")}
              disabled={decision?.decision === "rejected"}
              className="btn"
            >
              Reject
            </button>
            <button
              type="button"
              onClick={() => onDecide(exception.id, "approved")}
              disabled={decision?.decision === "approved"}
              className="btn btn-primary"
            >
              Approve
            </button>
          </div>

          {decision && (
            <p className="mt-2.5 text-[12.5px] text-faint">
              Marked {decision.decision} at {formatTimestamp(decision.created_at)}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="legend">{label}</span>
      {/* The values carry newlines, so they need pre-line to stay as lines. */}
      <div className="mt-2 whitespace-pre-line text-muted">{children}</div>
    </div>
  );
}
