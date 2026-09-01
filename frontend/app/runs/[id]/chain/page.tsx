"use client";

import { use, useMemo, useState } from "react";
import { EvidenceList } from "@/components/EvidenceList";
import { Loading } from "@/components/Surface";
import type { components } from "@/lib/api/client";
import { useRun, useRunResult } from "@/lib/useRun";

type MatchGroup = components["schemas"]["MatchGroup"];

const STATUS_OPTIONS = ["all", "auto", "assisted", "open"] as const;
const PASS_OPTIONS = ["all", "P1", "P2", "P3", "P4", "LLM"] as const;

const IDS_SHOWN = 3;

/**
 * A settled chain can gather dozens of invoice and payment ids. Rendering all
 * of them turns every row into a paragraph, so the cell shows the first few and
 * the rest expand in place.
 */
function IdList({ ids }: { ids: string[] | null | undefined }) {
  const [expanded, setExpanded] = useState(false);

  if (!ids || ids.length === 0) return <span className="text-faint">-</span>;

  const hidden = ids.length - IDS_SHOWN;
  const shown = expanded ? ids : ids.slice(0, IDS_SHOWN);

  return (
    <span className="mono text-xs leading-relaxed">
      {shown.join(", ")}
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="ml-1.5 whitespace-nowrap text-accent hover:underline"
        >
          {expanded ? "show less" : `+${hidden} more`}
        </button>
      )}
    </span>
  );
}

const STATUS_TONE: Record<string, string> = {
  auto: "chip-tied",
  assisted: "chip-live",
  open: "chip-risk",
};

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
  if (!result) return <Loading />;

  return (
    <>
      {/* Segmented filters rather than selects: there are few enough options
          that showing them all beats hiding them behind a dropdown. */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <Segmented
          label="Status"
          options={STATUS_OPTIONS}
          value={status}
          onChange={(v) => setStatus(v as (typeof STATUS_OPTIONS)[number])}
        />
        <Segmented
          label="Pass"
          options={PASS_OPTIONS}
          value={pass}
          onChange={(v) => setPass(v as (typeof PASS_OPTIONS)[number])}
        />
        <span className="mono ml-auto text-xs text-faint">
          {groups.length} chain{groups.length === 1 ? "" : "s"}
        </span>
      </div>

      <section className="panel overflow-hidden">
        <div className="overflow-x-auto">
          <table className="grid-table">
            <thead>
              <tr>
                <th scope="col">Invoice</th>
                <th scope="col">Payment</th>
                <th scope="col">Settlement</th>
                <th scope="col">Bank line</th>
                <th scope="col">Status</th>
                <th scope="col">Pass</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => (
                <ChainRow key={group.id} group={group} />
              ))}
            </tbody>
          </table>
        </div>
        {groups.length === 0 && (
          <p className="px-5 py-10 text-center text-sm text-muted">No matched chains for this filter.</p>
        )}
      </section>

      <p className="text-xs leading-relaxed text-faint">
        Difficulty-class filtering is only meaningful against a synthetic corpus&apos;s known truth file
        and isn&apos;t exposed for a live run.
      </p>
    </>
  );
}

function Segmented({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly string[];
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="legend">{label}</span>
      <div
        role="group"
        aria-label={label}
        className="flex overflow-hidden rounded-[3px] border border-hairline-strong"
      >
        {options.map((option) => {
          const active = option === value;
          return (
            <button
              key={option}
              type="button"
              onClick={() => onChange(option)}
              aria-pressed={active}
              className={
                "min-h-8 border-r border-hairline px-3 text-[11px] font-medium uppercase tracking-[0.06em] transition-colors last:border-r-0 " +
                (active
                  ? "bg-accent text-[color:var(--on-accent)]"
                  : "bg-surface text-muted hover:text-foreground")
              }
            >
              {option === "all" ? "All" : option}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ChainRow({ group }: { group: MatchGroup }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className={open ? "row-selected" : ""}>
        <td className="max-w-[22ch] align-top">
          <IdList ids={group.invoice_ids} />
        </td>
        <td className="max-w-[22ch] align-top">
          <IdList ids={group.payment_ids} />
        </td>
        <td className="mono align-top text-xs">{group.settlement_id ?? "-"}</td>
        <td className="mono align-top text-xs">{group.bank_line_id ?? "-"}</td>
        <td className="align-top">
          <span className={`chip ${STATUS_TONE[group.status] ?? ""}`}>
            {group.status}
          </span>
        </td>
        <td className="mono align-top text-xs text-muted">{group.pass_id}</td>
        <td className="align-top text-right">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="btn btn-sm"
          >
            {open ? "Hide" : "Evidence"}
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7} className="bg-sunk">
            <EvidenceList evidence={group.evidence} />
          </td>
        </tr>
      )}
    </>
  );
}
