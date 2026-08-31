export function Stat({ label, value, mono = true }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-hairline py-3 text-sm">
      <dt className="text-muted">{label}</dt>
      <dd className={mono ? "font-mono tabular" : ""}>{value}</dd>
    </div>
  );
}
