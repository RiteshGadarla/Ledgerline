"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { UserBadge } from "@/components/UserBadge";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, Loading, PanelHead, Surface } from "@/components/Surface";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/client";
import { formatRupees } from "@/lib/money";
import { formatTimestamp } from "@/lib/time";

type RunOut = components["schemas"]["RunOut"];
type DatasetOut = components["schemas"]["DatasetOut"];

const ROLES = ["ledger", "gateway", "settlement", "bank"] as const;
const ROLE_LABEL: Record<string, string> = {
  ledger: "Ledger",
  gateway: "Gateway",
  settlement: "Settle",
  bank: "Bank",
};

function formatRate(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

/**
 * Adversarial mutations. Each one corrupts a copy of the dataset the way real
 * books get corrupted, and the ground truth is corrupted in lockstep, so
 * accuracy stays measurable after the sabotage rather than becoming
 * unknowable. The label says what goes wrong; the hint says what should
 * therefore show up in the exception list.
 */
const MUTATIONS: { value: string; label: string; hint: string }[] = [
  {
    value: "duplicate_payment",
    label: "Post a payment twice",
    hint: "should surface as a duplicate candidate",
  },
  { value: "delete_bank_line", label: "Lose a bank credit", hint: "the payout never landed" },
  {
    value: "alter_amount",
    label: "Edit an amount after the fact",
    hint: "the batch stops tying out",
  },
  { value: "scramble_narration", label: "Mangle a narration", hint: "the UTR becomes unreadable" },
  { value: "shift_date", label: "Post a credit weeks late", hint: "right money, wrong window" },
  {
    value: "inject_unrelated_credit",
    label: "Add money from nowhere",
    hint: "belongs to nobody in these books",
  },
  {
    value: "split_payment",
    label: "Split a capture across batches",
    hint: "half settles, half does not",
  },
];

function RunSurface() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [datasets, setDatasets] = useState<DatasetOut[] | null>(null);
  const [datasetId, setDatasetId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunOut[] | null>(null);
  const [mutations, setMutations] = useState<string[]>([]);

  useEffect(() => {
    api.GET("/runs").then(({ data }) => setRuns(data ?? []));
    api.GET("/datasets").then(({ data }) => {
      const ready = (data ?? []).filter((d) => d.status === "ready");
      setDatasets(ready);
      const requested = searchParams.get("dataset");
      if (requested && ready.some((d) => d.id === requested)) {
        setDatasetId(requested);
      } else if (ready.length > 0) {
        setDatasetId(ready[0].id);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const datasetById = Object.fromEntries((datasets ?? []).map((d) => [d.id, d]));
  const selected = datasetById[datasetId];

  async function closeTheBooks(event: React.FormEvent) {
    event.preventDefault();
    if (!datasetId) return;
    setSubmitting(true);
    setError(null);

    const { data, error: apiError } = await api.POST("/runs", {
      body: {
        source: "dataset",
        dataset_id: datasetId,
        mutations: mutations.length > 0 ? mutations : null,
      },
    });

    setSubmitting(false);
    if (apiError || !data) {
      setError("Could not start a run. Please try again.");
      return;
    }
    router.push(`/runs/${data.id}`);
  }

  return (
    <Surface
      crumb="Console"
      title={<span className="text-[15px] font-semibold tracking-[-0.015em]">Run</span>}
      tools={<UserBadge />}
      strip={[
        { label: "READY", tone: "var(--readout-hi)" },
        { label: "WORKER", value: "arq" },
        { label: "ENGINE", value: "verifier-gated" },
      ]}
    >
      {/* The one action this surface exists for, given its own instrument. */}
      <section className="panel">
        <PanelHead legend="Close the books" note="DETERMINISTIC · SEEDED · VERIFIER-GATED" />

        <div className="p-4">
          {datasets === null ? (
            <Loading label="Loading your datasets…" />
          ) : datasets.length === 0 ? (
            <EmptyState
              title="No ready dataset yet"
              body="A dataset needs at least one validated file before it can be run. Generate a synthetic corpus or upload your own."
              action={
                <Link href="/data" className="btn btn-primary">
                  Create a dataset
                </Link>
              }
            />
          ) : (
            <form onSubmit={closeTheBooks} className="flex flex-wrap items-end gap-4">
              <div className="min-w-64 flex-1">
                <label className="legend" htmlFor="run-dataset">
                  Dataset
                </label>
                <select
                  id="run-dataset"
                  value={datasetId}
                  onChange={(e) => setDatasetId(e.target.value)}
                  className="field mt-2"
                >
                  {datasets.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </div>

              {selected && (
                <div className="shrink-0">
                  <span className="legend">Coverage</span>
                  <div className="mt-2.5 flex gap-1.5">
                    {ROLES.map((role) => {
                      const present = selected.files.some((f) => f.role === role);
                      return (
                        <span
                          key={role}
                          className={"chip " + (present ? "chip-tied" : "")}
                          title={present ? `${ROLE_LABEL[role]} file present` : `No ${role} file`}
                        >
                          {ROLE_LABEL[role]}
                        </span>
                      );
                    })}
                  </div>
                </div>
              )}

              <button
                type="submit"
                disabled={submitting || !datasetId}
                className="btn btn-primary btn-lg"
              >
                {submitting ? "Starting…" : "Close the books"}
              </button>

              {/* Sabotage is opt-in and never the default: a clean run is what
                  the console is for, and a corrupted one has to be a choice
                  the operator made on purpose and can see they made. */}
              <div className="w-full border-t border-hairline pt-4">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="legend">Break it first</span>
                  <span className="text-[11.5px] text-faint">
                    Optional. Corrupts a copy of the data (the dataset itself is never touched)
                    and corrupts the answer key with it, so the score stays honest.
                  </span>
                </div>
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {MUTATIONS.map((mutation) => {
                    const on = mutations.includes(mutation.value);
                    return (
                      <button
                        key={mutation.value}
                        type="button"
                        aria-pressed={on}
                        title={mutation.hint}
                        onClick={() =>
                          setMutations((prev) =>
                            prev.includes(mutation.value)
                              ? prev.filter((m) => m !== mutation.value)
                              : [...prev, mutation.value],
                          )
                        }
                        className={
                          "rounded-[3px] border px-2.5 py-1.5 text-[12px] transition-colors " +
                          (on
                            ? "border-signal bg-signal-bg text-signal"
                            : "border-hairline text-muted hover:border-hairline-strong hover:text-foreground")
                        }
                      >
                        {mutation.label}
                      </button>
                    );
                  })}
                </div>
                {mutations.length > 0 && (
                  <p className="mt-2.5 text-[11.5px] text-muted">
                    {mutations.length} corruption{mutations.length === 1 ? "" : "s"} will be applied
                    in order. False matches must still be zero afterwards; that is the point of the
                    exercise.
                  </p>
                )}
              </div>
            </form>
          )}

          {error && (
            <p
              role="alert"
              className="mt-4 rounded-[3px] border border-signal bg-signal-bg px-3 py-2 text-sm text-signal"
            >
              {error}
            </p>
          )}
        </div>
      </section>

      <section className="panel">
        <PanelHead legend="Recent runs" note={runs ? `${runs.length} TOTAL` : undefined} />

        {runs === null ? (
          <div className="px-4">
            <Loading />
          </div>
        ) : runs.length === 0 ? (
          <p className="px-5 py-10 text-center text-sm text-muted">
            No runs yet. Close the books above to start one.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="grid-table">
              <thead>
                <tr>
                  <th scope="col">Run</th>
                  <th scope="col">Dataset</th>
                  <th scope="col">Started</th>
                  <th scope="col" className="!text-right">
                    Match rate
                  </th>
                  <th scope="col" className="!text-right">
                    At risk
                  </th>
                  <th scope="col">State</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr
                    key={run.id}
                    className="row-interactive"
                    onClick={() => router.push(`/runs/${run.id}`)}
                  >
                    <td>
                      <Link
                        href={`/runs/${run.id}`}
                        className="mono text-[12.5px] text-accent hover:underline"
                      >
                        {run.id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="text-muted">
                      <span className="flex items-center gap-2">
                        {run.source === "demo"
                          ? "Demo"
                          : (datasetById[run.dataset_id ?? ""]?.name ?? "Dataset")}
                        {/* A sabotaged run must never be read as a clean one. */}
                        {run.mutations && run.mutations.length > 0 && (
                          <span
                            className="chip border-signal text-signal"
                            title={run.mutations.join(", ")}
                          >
                            {run.mutations.length} broken
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="mono text-muted">{formatTimestamp(run.created_at)}</td>
                    <td className="mono text-right">
                      {run.metrics ? formatRate(run.metrics.auto_rate) : "-"}
                    </td>
                    <td
                      className={"mono text-right " + (run.metrics ? "text-signal" : "text-faint")}
                    >
                      {run.metrics ? formatRupees(run.metrics.amount_at_risk) : "-"}
                    </td>
                    <td>
                      <StatusBadge state={run.state} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </Surface>
  );
}

export default function RunPage() {
  return (
    <RequireAuth>
      <RunSurface />
    </RequireAuth>
  );
}
