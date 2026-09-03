"use client";

import { useEffect, useState } from "react";

const TICK_MS = 100;

/**
 * A wall clock that ticks while a run is in flight and stops when it is not.
 *
 * It returns the current instant rather than a duration, because more than
 * one figure is measured against it: the run's total elapsed time and the
 * age of whichever pipeline stage is live. Callers subtract; the clock only
 * says what time it is, which keeps every one of those figures derived
 * during render from a single value React knows about.
 *
 * A tenth of a second is the smallest digit worth showing -- fine enough that
 * the clock visibly moves during a long stage, coarse enough not to read as
 * noise -- so that is also the tick.
 */
export function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, [running]);

  return now;
}

/**
 * When the clock started. The API's `created_at` is the honest answer -- it
 * survives a reload and covers the time the job spent queued before anyone
 * was watching -- but it is the *server's* clock, and a browser whose own
 * clock is badly set would render a run that started three hours ago or has
 * not started yet. Anything that implausible falls back to the local
 * reference the caller supplies.
 */
const IMPLAUSIBLE_MS = 24 * 60 * 60 * 1000;

export function runStartedAt(
  createdAt: string | null | undefined,
  now: number,
  fallback: number,
): number {
  if (!createdAt) return fallback;
  const parsed = Date.parse(createdAt);
  if (Number.isNaN(parsed)) return fallback;
  const age = now - parsed;
  return age < 0 || age > IMPLAUSIBLE_MS ? fallback : parsed;
}

/**
 * How long a finished run took, measured between two of the server's own
 * timestamps. Reading a terminal run's duration off the browser clock would
 * report the time since it was created -- which keeps growing long after the
 * run stopped -- so this is the only correct source once it is over.
 */
export function completedDuration(
  createdAt: string | null | undefined,
  updatedAt: string | null | undefined,
): number | null {
  if (!createdAt || !updatedAt) return null;
  const start = Date.parse(createdAt);
  const end = Date.parse(updatedAt);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  return end - start;
}

/** `0:07.4` -- minutes always, tenths always. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, ms);
  const minutes = Math.floor(total / 60_000);
  const seconds = Math.floor((total % 60_000) / 1000);
  const tenths = Math.floor((total % 1000) / 100);
  return `${minutes}:${String(seconds).padStart(2, "0")}.${tenths}`;
}

/** A stage duration, where seconds are the unit that matters: `1.4s`. */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
