"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { api } from "./api/client";
import type { components } from "./api/client";

type RunOut = components["schemas"]["RunOut"];
type RunResultOut = components["schemas"]["RunResultOut"];

const POLL_MS = 2000;

function isTerminal(state: string): boolean {
  return state === "complete" || state === "failed";
}

/**
 * One poller per run, shared by every component watching it.
 *
 * Four surfaces read the same run at once -- the run shell around the tabs,
 * plus whichever tab is mounted inside it -- and each used to run its own
 * two-second timer against the same URL. This registry keeps a single timer
 * per run id and fans the row out to every subscriber, so the request count
 * no longer scales with how many components happen to care.
 *
 * It also stops entirely while the tab is in the background: a run left open
 * in a forgotten tab should cost nothing, and the poll resumes with a fresh
 * read the moment the tab is looked at again.
 */
type Watch = {
  run: RunOut | null;
  /** Store-changed notifiers, one per mounted subscriber. */
  subscribers: Set<() => void>;
  timer: ReturnType<typeof setTimeout> | null;
  /** Guards against two polls in flight after a visibility change. */
  inFlight: boolean;
};

const watches = new Map<string, Watch>();

function hidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

async function pollOnce(runId: string) {
  const watch = watches.get(runId);
  if (!watch || watch.inFlight) return;

  watch.inFlight = true;
  const { data } = await api.GET("/runs/{run_id}", { params: { path: { run_id: runId } } });
  watch.inFlight = false;

  // Unsubscribed while the request was out; drop the answer on the floor.
  if (!watches.has(runId)) return;
  if (data) {
    watch.run = data;
    for (const notify of watch.subscribers) notify();
  }
  schedule(runId);
}

function schedule(runId: string) {
  const watch = watches.get(runId);
  if (!watch || watch.timer !== null) return;
  // A finished run never changes again, and a backgrounded tab is not
  // looking; in both cases the next read is driven by an event, not a timer.
  if (watch.run && isTerminal(watch.run.state)) return;
  if (hidden()) return;
  watch.timer = setTimeout(() => {
    const current = watches.get(runId);
    if (!current) return;
    current.timer = null;
    // Backgrounded between scheduling and firing; the visibility handler
    // owns the next read now.
    if (hidden()) return;
    void pollOnce(runId);
  }, POLL_MS);
}

/** Read a run now, out of band -- used when the live stream reports a state
 *  change and the shared timer would otherwise sit on it for two seconds. */
export function refreshRun(runId: string) {
  if (watches.has(runId)) void pollOnce(runId);
}

function wakeAll() {
  if (hidden()) return;
  for (const runId of watches.keys()) void pollOnce(runId);
}

let wakeBound = false;

function bindWake() {
  if (wakeBound || typeof document === "undefined") return;
  wakeBound = true;
  document.addEventListener("visibilitychange", wakeAll);
}

export function useRun(runId: string): RunOut | null {
  // useSyncExternalStore rather than an effect that copies the store into
  // component state: a surface mounting into a watch another surface already
  // established reads the row it is holding on the very first render, with
  // no second pass and no flash of "Loading".
  const subscribe = useCallback(
    (onStoreChange: () => void) => {
      bindWake();
      let watch = watches.get(runId);
      if (!watch) {
        watch = { run: null, subscribers: new Set(), timer: null, inFlight: false };
        watches.set(runId, watch);
      }
      watch.subscribers.add(onStoreChange);
      void pollOnce(runId);

      return () => {
        const current = watches.get(runId);
        if (!current) return;
        current.subscribers.delete(onStoreChange);
        if (current.subscribers.size === 0) {
          if (current.timer !== null) clearTimeout(current.timer);
          watches.delete(runId);
        }
      };
    },
    [runId],
  );

  // The row object is replaced only when a poll brings a new one, so this is
  // reference-stable between updates, which is what the store contract asks
  // for. `null` on the server: nothing is polled during prerender.
  const snapshot = useCallback(() => watches.get(runId)?.run ?? null, [runId]);

  return useSyncExternalStore(subscribe, snapshot, () => null);
}

/**
 * A finished run's result is immutable, so it is fetched once per run id and
 * held for the session: moving between the Chain and Exceptions tabs re-reads
 * nothing. The in-flight map means two tabs mounting together still issue one
 * request between them rather than racing.
 */
const results = new Map<string, RunResultOut>();
const resultRequests = new Map<string, Promise<RunResultOut | null>>();

function loadResult(runId: string): Promise<RunResultOut | null> {
  const cached = results.get(runId);
  if (cached) return Promise.resolve(cached);

  const pending = resultRequests.get(runId);
  if (pending) return pending;

  const request = api
    .GET("/runs/{run_id}/result", { params: { path: { run_id: runId } } })
    .then(({ data }) => {
      if (data) results.set(runId, data);
      return data ?? null;
    })
    .finally(() => resultRequests.delete(runId));

  resultRequests.set(runId, request);
  return request;
}

export function useRunResult(runId: string, ready: boolean): RunResultOut | null {
  const [result, setResult] = useState<RunResultOut | null>(() => results.get(runId) ?? null);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    void loadResult(runId).then((data) => {
      if (!cancelled && data) setResult(data);
    });
    return () => {
      cancelled = true;
    };
  }, [runId, ready]);

  return result;
}
