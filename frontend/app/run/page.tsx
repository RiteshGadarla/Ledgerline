"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/client";

type RunOut = components["schemas"]["RunOut"];

function RunSurface() {
  const router = useRouter();
  const [seed, setSeed] = useState("1001");
  const [size, setSize] = useState("150");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunOut[] | null>(null);

  useEffect(() => {
    api.GET("/runs").then(({ data }) => setRuns(data ?? []));
  }, []);

  async function closeTheBooks(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    const { data, error: apiError } = await api.POST("/runs", {
      body: {
        source: "demo",
        seed: seed ? Number(seed) : undefined,
        size: size ? Number(size) : undefined,
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
    <div className="flex flex-col gap-10">
      <section>
        <h1 className="text-lg font-semibold">Run</h1>
        <p className="mt-1 text-sm text-muted">
          Close the books on a synthetic demo corpus. Bring-your-own-data runs are coming soon.
        </p>

        <form onSubmit={closeTheBooks} className="mt-6 flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1 text-sm">
            Seed
            <input
              className="w-32 border border-hairline px-3 py-2 font-mono text-sm tabular"
              inputMode="numeric"
              value={seed}
              onChange={(e) => setSeed(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Size
            <input
              className="w-32 border border-hairline px-3 py-2 font-mono text-sm tabular"
              inputMode="numeric"
              value={size}
              onChange={(e) => setSize(e.target.value)}
            />
          </label>
          <button
            type="submit"
            disabled={submitting}
            className="border border-foreground bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
          >
            {submitting ? "Starting…" : "Close the books"}
          </button>
        </form>
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
