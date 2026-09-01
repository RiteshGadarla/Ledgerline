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
  const isFailed = state === "failed";
  const isComplete = state === "complete";
  const inFlight = !isFailed && !isComplete;

  const tone = isFailed ? "chip-risk" : isComplete ? "chip-tied" : "chip-live";

  return (
    <span className={`chip ${tone}`}>
      <span aria-hidden className={"dot " + (inFlight ? "pulse-dot" : "")} />
      {LABELS[state] ?? state}
    </span>
  );
}
