"use client";

import { useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import type { components } from "@/lib/api/client";

type PreviewOut = components["schemas"]["PreviewOut"];
type ValidateOut = components["schemas"]["ValidateOut"];
type FieldMappingOut = components["schemas"]["FieldMappingOut"];

const ROLES = [
  { value: "ledger", label: "Ledger (invoices)" },
  { value: "gateway", label: "Gateway (payments)" },
  { value: "settlement", label: "Settlement" },
  { value: "bank", label: "Bank statement" },
];

// FormData uploads don't fit openapi-fetch's typed body shape cleanly (the
// generated type for a multipart file field is `string`, not `File`), so
// these two calls go through plain fetch against the same proxy route and
// are cast to the response types the generated schema already describes.
async function postForm<T>(path: string, formData: FormData): Promise<{ data?: T; error?: { detail?: string } }> {
  const response = await fetch(`/api${path}`, { method: "POST", body: formData });
  const body = await response.json();
  return response.ok ? { data: body as T } : { error: body as { detail?: string } };
}

function DataSurface() {
  const [role, setRole] = useState(ROLES[0].value);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewOut | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string | null>>({});
  const [report, setReport] = useState<ValidateOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function runPreview(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setReport(null);

    const formData = new FormData();
    formData.set("role", role);
    formData.set("file", file);
    const { data, error: apiError } = await postForm<PreviewOut>("/data/preview", formData);

    setBusy(false);
    if (!data) {
      setError(apiError?.detail ?? "Could not read that file.");
      return;
    }
    setPreview(data);
    setOverrides(Object.fromEntries(data.mapping.map((m) => [m.source_header, m.canonical_field])));
  }

  async function runValidate() {
    if (!file || !preview) return;
    setBusy(true);
    setError(null);

    const mapping = preview.headers.map((header) => ({
      source_header: header,
      canonical_field: overrides[header] ?? null,
      confidence: preview.mapping.find((m) => m.source_header === header)?.confidence ?? 0,
    }));

    const formData = new FormData();
    formData.set("role", role);
    formData.set("mapping", JSON.stringify(mapping));
    formData.set("file", file);
    const { data, error: apiError } = await postForm<ValidateOut>("/data/validate", formData);

    setBusy(false);
    if (!data) {
      setError(apiError?.detail ?? "Could not validate that file.");
      return;
    }
    setReport(data);
  }

  return (
    <div className="flex flex-col gap-8">
      <section>
        <h1 className="text-lg font-semibold">Data</h1>
        <p className="mt-1 text-sm text-muted">
          Upload a CSV, XLSX, or bank-statement PDF to preview its column mapping. Uploads aren&apos;t saved as
          reusable datasets yet -- runs against your own data are coming soon.
        </p>

        <form onSubmit={runPreview} className="mt-6 flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1 text-sm">
            Role
            <select value={role} onChange={(e) => setRole(e.target.value)} className="border border-hairline px-2 py-2">
              {ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            File
            <input
              type="file"
              accept=".csv,.xlsx,.pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-sm"
            />
          </label>
          <button
            type="submit"
            disabled={!file || busy}
            className="border border-foreground bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
          >
            {busy ? "Working…" : "Preview mapping"}
          </button>
        </form>
        {error && (
          <p role="alert" className="mt-2 text-sm text-signal">
            {error}
          </p>
        )}
      </section>

      {preview && (
        <section>
          <h2 className="text-sm font-semibold text-muted">Confirm column mapping</h2>
          <table className="mt-3 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-hairline text-left text-muted">
                <th scope="col" className="py-2 font-normal">Column</th>
                <th scope="col" className="py-2 font-normal">Sample value</th>
                <th scope="col" className="py-2 font-normal">Maps to</th>
              </tr>
            </thead>
            <tbody>
              {preview.headers.map((header) => (
                <MappingRow
                  key={header}
                  header={header}
                  sample={preview.sample_rows[0]?.[header] ?? ""}
                  canonicalFields={preview.canonical_fields}
                  value={overrides[header] ?? null}
                  mapping={preview.mapping.find((m) => m.source_header === header)}
                  onChange={(next) => setOverrides((prev) => ({ ...prev, [header]: next }))}
                />
              ))}
            </tbody>
          </table>
          <button
            type="button"
            onClick={runValidate}
            disabled={busy}
            className="mt-4 border border-foreground bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
          >
            {busy ? "Validating…" : "Confirm and check file health"}
          </button>
        </section>
      )}

      {report && (
        <section>
          <h2 className="text-sm font-semibold text-muted">File health</h2>
          <p className="mt-2 text-sm">
            <span className="font-mono tabular">{report.valid_count}</span> of{" "}
            <span className="font-mono tabular">{report.total_rows}</span> rows are usable.
          </p>
          {report.errors.length > 0 && (
            <table className="mt-3 w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-hairline text-left text-muted">
                  <th scope="col" className="py-2 font-normal">Row</th>
                  <th scope="col" className="py-2 font-normal">Reason</th>
                </tr>
              </thead>
              <tbody>
                {report.errors.map((e) => (
                  <tr key={e.row_number} className="border-b border-hairline">
                    <td className="py-2 font-mono tabular">{e.row_number}</td>
                    <td className="py-2">{e.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </div>
  );
}

function MappingRow({
  header,
  sample,
  canonicalFields,
  value,
  mapping,
  onChange,
}: {
  header: string;
  sample: string;
  canonicalFields: string[];
  value: string | null;
  mapping: FieldMappingOut | undefined;
  onChange: (next: string | null) => void;
}) {
  return (
    <tr className="border-b border-hairline">
      <td className="py-2 font-mono">{header}</td>
      <td className="py-2 font-mono text-muted">{sample}</td>
      <td className="py-2">
        <select
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value || null)}
          className="border border-hairline px-2 py-1"
        >
          <option value="">(ignore)</option>
          {canonicalFields.map((field) => (
            <option key={field} value={field}>
              {field}
            </option>
          ))}
        </select>
        {mapping && <span className="ml-2 text-xs text-muted">confidence {mapping.confidence.toFixed(2)}</span>}
      </td>
    </tr>
  );
}

export default function DataPage() {
  return (
    <RequireAuth>
      <DataSurface />
    </RequireAuth>
  );
}
