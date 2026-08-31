const LABELS: Record<string, string> = {
  queued: "Queued",
  normalising: "Normalising",
  matching: "Matching",
  triaging: "Triaging",
  explaining: "Explaining",
  scoring: "Scoring",
  complete: "Complete",
  failed: "Failed",
};

export function StatusBadge({ state }: { state: string }) {
  const isTerminal = state === "complete" || state === "failed";
  const isFailed = state === "failed";
  return (
    <span
      className={
        "inline-flex items-center gap-1.5 border px-2 py-0.5 text-xs " +
        (isFailed
          ? "border-signal text-signal"
          : isTerminal
            ? "border-hairline text-foreground"
            : "border-hairline text-muted")
      }
    >
      {!isTerminal && <span aria-hidden className="inline-block h-1.5 w-1.5 rounded-full bg-current" />}
      {LABELS[state] ?? state}
    </span>
  );
}
