import type { components } from "@/lib/api/client";

type Evidence = components["schemas"]["Evidence"];

export function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (evidence.length === 0) {
    return <p className="text-xs text-faint">No evidence recorded.</p>;
  }
  return (
    <ul className="flex flex-col gap-1.5 p-3.5 text-xs">
      {evidence.map((item, index) => (
        <li
          key={index}
          className="mono flex flex-wrap items-baseline gap-x-2 rounded-[3px] border border-hairline bg-surface px-2.5 py-1.5"
        >
          <span className="text-muted">{item.field}</span>
          <span className="text-faint">=</span>
          <span className="text-accent">&ldquo;{item.value}&rdquo;</span>
          <span className="text-faint">({item.source_id})</span>
        </li>
      ))}
    </ul>
  );
}
