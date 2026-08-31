"use client";

import { useEffect, useState } from "react";
import { api } from "./api/client";
import type { components } from "./api/client";

type RunOut = components["schemas"]["RunOut"];
type RunResultOut = components["schemas"]["RunResultOut"];

const POLL_MS = 2000;

export function useRun(runId: string): RunOut | null {
  const [run, setRun] = useState<RunOut | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      const { data } = await api.GET("/runs/{run_id}", { params: { path: { run_id: runId } } });
      if (cancelled) return;
      if (data) setRun(data);
      if (data && data.state !== "complete" && data.state !== "failed") {
        timer = setTimeout(poll, POLL_MS);
      }
    }
    poll();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId]);

  return run;
}

export function useRunResult(runId: string, ready: boolean): RunResultOut | null {
  const [result, setResult] = useState<RunResultOut | null>(null);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    api.GET("/runs/{run_id}/result", { params: { path: { run_id: runId } } }).then(({ data }) => {
      if (!cancelled && data) setResult(data);
    });
    return () => {
      cancelled = true;
    };
  }, [runId, ready]);

  return result;
}
