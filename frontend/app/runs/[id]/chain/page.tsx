"use client";

import { use, useMemo, useState } from "react";
import { EvidenceList } from "@/components/EvidenceList";
import type { components } from "@/lib/api/client";
import { useRun, useRunResult } from "@/lib/useRun";

type MatchGroup = components["schemas"]["MatchGroup"];

const STATUS_OPTIONS = ["all", "auto", "assisted", "open"] as const;
const PASS_OPTIONS = ["all", "P1", "P2", "P3", "P4", "LLM"] as const;

function joinIds(ids: string[] | null | undefined): string {
  if (!ids || ids.length === 0) return "—";
  return ids.join(", ");
}

export default function ChainPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const run = useRun(id);
  const result = useRunResult(id, run?.state === "complete");
  const [status, setStatus] = useState<(typeof STATUS_OPTIONS)[number]>("all");
  const [pass, setPass] = useState<(typeof PASS_OPTIONS)[number]>("all");

  const groups = useMemo(() => {
    if (!result) return [];
    return result.groups.filter(
      (g) => (status === "all" || g.status === status) && (pass === "all" || g.pass_id === pass),
    );
  }, [result, status, pass]);

  if (run?.state === "failed") return <p className="text-sm text-signal">This run failed: {run.error}</p>;
  if (!result) return <p className="text-sm text-muted">Loading…</p>;

  return (
    <div>
      <div className="flex flex-wrap gap-4 text-sm">
        <label className="flex items-center gap-2">
          Status
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value as (typeof STATUS_OPTIONS)[number])}
            className="border border-hairline px-2 py-1"
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option === "all" ? "All" : option}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          Pass
          <select
            value={pass}
            onChange={(e) => setPass(e.target.value as (typeof PASS_OPTIONS)[number])}
            className="border border-hairline px-2 py-1"
          >
            {PASS_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option === "all" ? "All" : option}
              </option>
            ))}
          </select>
        </label>
      </div>

      <table className="mt-4 w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-hairline text-left text-muted">
            <th scope="col" className="py-2 font-normal">Invoice</th>
            <th scope="col" className="py-2 font-normal">Payment</th>
            <th scope="col" className="py-2 font-normal">Settlement</th>
            <th scope="col" className="py-2 font-normal">Bank line</th>
            <th scope="col" className="py-2 font-normal">Status</th>
            <th scope="col" className="py-2 font-normal">Pass</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <ChainRow key={group.id} group={group} />
          ))}
        </tbody>
      </table>
      {groups.length === 0 && <p className="mt-3 text-sm text-muted">No matched chains for this filter.</p>}

      <p className="mt-2 text-xs text-muted">
        Difficulty-class filtering is only meaningful against a synthetic corpus&apos;s known truth file and isn&apos;t
        exposed for a live run.
      </p>
    </div>
  );
}

function ChainRow({ group }: { group: MatchGroup }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="border-b border-hairline font-mono text-xs">
        <td className="py-2">{joinIds(group.invoice_ids)}</td>
        <td className="py-2">{joinIds(group.payment_ids)}</td>
        <td className="py-2">{group.settlement_id ?? "—"}</td>
        <td className="py-2">{group.bank_line_id ?? "—"}</td>
        <td className="py-2 font-sans">{group.status}</td>
        <td className="py-2 font-sans">
          {group.pass_id}{" "}
          <button type="button" onClick={() => setOpen((v) => !v)} className="ml-2 underline">
            {open ? "hide evidence" : "evidence"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-hairline">
          <td colSpan={6} className="py-2">
            <EvidenceList evidence={group.evidence} />
          </td>
        </tr>
      )}
    </>
  );
}
