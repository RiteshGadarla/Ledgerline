import type { components } from "@/lib/api/client";

type Evidence = components["schemas"]["Evidence"];

export function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (evidence.length === 0) {
    return <p className="text-xs text-muted">No evidence recorded.</p>;
  }
  return (
    <ul className="flex flex-col gap-1 text-xs">
      {evidence.map((item, index) => (
        <li key={index} className="font-mono">
          <span className="text-muted">{item.field}</span> = &ldquo;{item.value}&rdquo;{" "}
          <span className="text-muted">({item.source_id})</span>
        </li>
      ))}
    </ul>
  );
}
