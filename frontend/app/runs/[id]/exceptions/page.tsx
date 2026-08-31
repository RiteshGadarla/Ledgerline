"use client";

import { use, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/client";
import { formatRupees } from "@/lib/money";
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
  if (!result) return <p className="text-sm text-muted">Loading…</p>;
  if (sorted.length === 0) return <p className="text-sm text-muted">No open exceptions -- everything tied out.</p>;

  return (
    <ul className="flex flex-col gap-3">
      {sorted.map((exc) => (
        <ExceptionCard key={exc.id} exception={exc} decision={decisions[exc.id]} onDecide={decide} />
      ))}
    </ul>
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
    <li className="border border-hairline">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-4 px-3 py-3 text-left text-sm"
      >
        <span>
          <span className="border border-signal px-1.5 py-0.5 text-xs text-signal">{exception.code}</span>{" "}
          {exception.explanation ?? exception.suggested_action}
        </span>
        <span className="font-mono tabular">{formatRupees(exception.amount_at_risk)}</span>
      </button>

      {open && (
        <div className="border-t border-hairline px-3 py-3 text-sm">
          <p>
            <span className="text-muted">Records:</span>{" "}
            {exception.records.map((r) => `${r.kind}:${r.id}`).join(", ")}
          </p>
          <p className="mt-1">
            <span className="text-muted">Attempted passes:</span> {exception.attempted.join(", ")}
          </p>
          {exception.suggested_action && (
            <p className="mt-1">
              <span className="text-muted">Suggested action:</span> {exception.suggested_action}
            </p>
          )}
          {exception.rejected_proposal && (
            <p className="mt-1">
              <span className="text-muted">Rejected proposal ({exception.rejected_proposal.proposed_by}):</span>{" "}
              failed check &ldquo;{exception.rejected_proposal.failed_check}&rdquo;
            </p>
          )}

          <div className="mt-3 flex items-center gap-3">
            <button
              type="button"
              onClick={() => onDecide(exception.id, "approved")}
              disabled={decision?.decision === "approved"}
              className="border border-hairline px-3 py-1.5 text-xs hover:border-foreground disabled:opacity-50"
            >
              Approve
            </button>
            <button
              type="button"
              onClick={() => onDecide(exception.id, "rejected")}
              disabled={decision?.decision === "rejected"}
              className="border border-hairline px-3 py-1.5 text-xs hover:border-foreground disabled:opacity-50"
            >
              Reject
            </button>
            {decision && (
              <span className="text-xs text-muted">
                Marked {decision.decision} at {new Date(decision.created_at).toLocaleString()}
              </span>
            )}
          </div>
        </div>
      )}
    </li>
  );
}
