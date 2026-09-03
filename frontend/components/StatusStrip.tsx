import { Credit } from "@/components/Credit";

/**
 * The instrument's bottom line. Dark in both themes, monospace, segments
 * divided by hairlines; it carries the facts you want visible without
 * looking for them: what state the run is in, and the hash that makes it
 * reproducible. Every value arrives already computed by the API.
 */
export function StatusStrip({ segments }: { segments: { label: string; value?: React.ReactNode; tone?: string }[] }) {
  return (
    <div className="strip">
      {segments.map((seg, i) => (
        <span key={i} className="strip-seg">
          {seg.tone && <span aria-hidden className="dot" style={{ color: seg.tone }} />}
          {seg.value === undefined ? <b>{seg.label}</b> : <>{seg.label} <b>{seg.value}</b></>}
        </span>
      ))}
      <Credit />
    </div>
  );
}
