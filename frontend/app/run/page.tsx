"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/client";

type RunOut = components["schemas"]["RunOut"];
type DatasetOut = components["schemas"]["DatasetOut"];

function RunSurface() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [datasets, setDatasets] = useState<DatasetOut[] | null>(null);
  const [datasetId, setDatasetId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunOut[] | null>(null);

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

  async function closeTheBooks(event: React.FormEvent) {
    event.preventDefault();
    if (!datasetId) return;
    setSubmitting(true);
    setError(null);

    const { data, error: apiError } = await api.POST("/runs", {
      body: { source: "dataset", dataset_id: datasetId },
    });

    setSubmitting(false);
    if (apiError || !data) {
      setError("Could not start a run. Please try again.");
      return;
    }
    router.push(`/runs/${data.id}`);
  }

  return (
    <div className="flex flex-col gap-10">
      <section>
        <h1 className="text-lg font-semibold">Run</h1>
        <p className="mt-1 text-sm text-muted">Choose one of your datasets and close the books against it.</p>

        {datasets === null ? (
          <p className="mt-6 text-sm text-muted">Loading your datasets…</p>
        ) : datasets.length === 0 ? (
          <div className="mt-6 border border-hairline p-4">
            <p className="text-sm text-muted">You don&apos;t have a ready dataset yet.</p>
            <a
              href="/data"
              className="mt-3 inline-block border border-foreground bg-foreground px-4 py-2 text-sm font-medium text-background"
            >
              Go create one
            </a>
          </div>
        ) : (
          <form onSubmit={closeTheBooks} className="mt-6 flex flex-wrap items-end gap-4">
            <label className="flex flex-col gap-1 text-sm">
              Dataset
              <select
                value={datasetId}
                onChange={(e) => setDatasetId(e.target.value)}
                className="w-64 border border-hairline px-3 py-2 text-sm"
              >
                {datasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              disabled={submitting || !datasetId}
              className="border border-foreground bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
            >
              {submitting ? "Starting…" : "Close the books"}
            </button>
          </form>
        )}
        {error && (
          <p role="alert" className="mt-2 text-sm text-signal">
            {error}
          </p>
        )}
      </section>

      <section>
        <h2 className="text-sm font-semibold text-muted">Recent runs</h2>
        {runs === null ? (
          <p className="mt-3 text-sm text-muted">Loading…</p>
        ) : runs.length === 0 ? (
          <p className="mt-3 text-sm text-muted">No runs yet -- close the books above to start one.</p>
        ) : (
          <table className="mt-3 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-hairline text-left text-muted">
                <th scope="col" className="py-2 font-normal">Run</th>
                <th scope="col" className="py-2 font-normal">Source</th>
                <th scope="col" className="py-2 font-normal">Started</th>
                <th scope="col" className="py-2 font-normal">Status</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="border-b border-hairline">
                  <td className="py-2">
                    <a href={`/runs/${run.id}`} className="font-mono underline">
                      {run.id.slice(0, 8)}
                    </a>
                  </td>
                  <td className="py-2 text-muted">
                    {run.source === "demo" ? "Demo" : datasetById[run.dataset_id ?? ""]?.name ?? "Dataset"}
                  </td>
                  <td className="py-2 text-muted">{new Date(run.created_at).toLocaleString()}</td>
                  <td className="py-2">
                    <StatusBadge state={run.state} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

export default function RunPage() {
  return (
    <RequireAuth>
      <RunSurface />
    </RequireAuth>
  );
}
