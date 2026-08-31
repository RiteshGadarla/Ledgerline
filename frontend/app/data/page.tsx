"use client";

import { useEffect, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/client";

type DatasetOut = components["schemas"]["DatasetOut"];
type DatasetFileOut = components["schemas"]["DatasetFileOut"];
type DatasetRecordsOut = components["schemas"]["DatasetRecordsOut"];
type PreviewOut = components["schemas"]["PreviewOut"];
type FieldMappingOut = components["schemas"]["FieldMappingOut"];

const ROLES: { value: string; label: string }[] = [
  { value: "ledger", label: "Ledger (invoices)" },
  { value: "gateway", label: "Gateway (payments)" },
  { value: "settlement", label: "Settlement" },
  { value: "bank", label: "Bank statement" },
];

const RECORDS_PAGE_SIZE = 20;

// FormData uploads don't fit openapi-fetch's typed body shape cleanly (the
// generated type for a multipart file field is `string`, not `File`), so
// these go through plain fetch against the same proxy route and are cast to
// the response types the generated schema already describes.
async function postForm<T>(path: string, formData: FormData): Promise<{ data?: T; error?: { detail?: string } }> {
  const response = await fetch(`/api${path}`, { method: "POST", body: formData });
  const body = await response.json();
  return response.ok ? { data: body as T } : { error: body as { detail?: string } };
}

function DataSurface() {
  const [datasets, setDatasets] = useState<DatasetOut[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState<"generate" | "upload" | null>(null);

  async function refreshDatasets(): Promise<void> {
    const { data } = await api.GET("/datasets");
    setDatasets(data ?? []);
  }

  useEffect(() => {
    api.GET("/datasets").then(({ data }) => setDatasets(data ?? []));
  }, []);

  return (
    <div className="flex flex-col gap-10">
      <section>
        <h1 className="text-lg font-semibold">Data</h1>
        <p className="mt-1 text-sm text-muted">
          Generate a synthetic corpus, or upload your own ledger, gateway, settlement, and bank files. Either way it
          becomes a reusable dataset you can close the books against from the Run page.
        </p>

        {creating === null ? (
          <div className="mt-6 flex gap-3">
            <button
              type="button"
              onClick={() => setCreating("generate")}
              className="border border-foreground bg-foreground px-4 py-2 text-sm font-medium text-background"
            >
              Generate a dataset
            </button>
            <button
              type="button"
              onClick={() => setCreating("upload")}
              className="border border-hairline px-4 py-2 text-sm font-medium"
            >
              Upload your own files
            </button>
          </div>
        ) : creating === "generate" ? (
          <GenerateForm
            onDone={async (dataset) => {
              await refreshDatasets();
              setSelectedId(dataset.id);
              setCreating(null);
            }}
            onCancel={() => setCreating(null)}
          />
        ) : (
          <CreateUploadedForm
            onDone={async (dataset) => {
              await refreshDatasets();
              setSelectedId(dataset.id);
              setCreating(null);
            }}
            onCancel={() => setCreating(null)}
          />
        )}
      </section>

      <section>
        <h2 className="text-sm font-semibold text-muted">Your datasets</h2>
        {datasets === null ? (
          <p className="mt-3 text-sm text-muted">Loading…</p>
        ) : datasets.length === 0 ? (
          <p className="mt-3 text-sm text-muted">No datasets yet -- generate or upload one above.</p>
        ) : (
          <table className="mt-3 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-hairline text-left text-muted">
                <th scope="col" className="py-2 font-normal">Name</th>
                <th scope="col" className="py-2 font-normal">Source</th>
                <th scope="col" className="py-2 font-normal">Files</th>
                <th scope="col" className="py-2 font-normal">Status</th>
                <th scope="col" className="py-2 font-normal">Created</th>
              </tr>
            </thead>
            <tbody>
              {datasets.map((dataset) => (
                <tr
                  key={dataset.id}
                  onClick={() => setSelectedId(dataset.id === selectedId ? null : dataset.id)}
                  className={
                    "cursor-pointer border-b border-hairline " + (dataset.id === selectedId ? "bg-hairline/20" : "")
                  }
                >
                  <td className="py-2 font-medium">{dataset.name}</td>
                  <td className="py-2 text-muted">{dataset.source}</td>
                  <td className="py-2 font-mono text-xs tabular">
                    {dataset.files.filter((f) => f.valid_count > 0).length}/4
                  </td>
                  <td className="py-2">
                    <DatasetStatusPill status={dataset.status} />
                  </td>
                  <td className="py-2 text-muted">{new Date(dataset.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {selectedId && (
        <DatasetDetail key={selectedId} datasetId={selectedId} onChanged={refreshDatasets} onClose={() => setSelectedId(null)} />
      )}
    </div>
  );
}

function DatasetStatusPill({ status }: { status: string }) {
  const ready = status === "ready";
  return (
    <span
      className={"inline-flex items-center gap-1.5 border px-2 py-0.5 text-xs " + (ready ? "border-hairline" : "border-hairline text-muted")}
    >
      <span aria-hidden className={"inline-block h-1.5 w-1.5 rounded-full " + (ready ? "bg-current" : "bg-transparent border border-current")} />
      {ready ? "Ready" : "Incomplete"}
    </span>
  );
}

function GenerateForm({
  onDone,
  onCancel,
}: {
  onDone: (dataset: DatasetOut) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("Generated corpus");
  const [seed, setSeed] = useState("");
  const [size, setSize] = useState("150");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const { data, error: apiError } = await api.POST("/datasets", {
      body: {
        name,
        source: "generated",
        seed: seed ? Number(seed) : undefined,
        size: size ? Number(size) : undefined,
      },
    });
    setBusy(false);
    if (!data) {
      setError(apiError && typeof apiError.detail === "string" ? apiError.detail : "Could not generate a dataset.");
      return;
    }
    onDone(data);
  }

  return (
    <form onSubmit={submit} className="mt-6 flex flex-wrap items-end gap-4 border border-hairline p-4">
      <label className="flex flex-col gap-1 text-sm">
        Name
        <input
          className="w-56 border border-hairline px-3 py-2 text-sm"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Seed (optional)
        <input
          className="w-32 border border-hairline px-3 py-2 font-mono text-sm tabular"
          inputMode="numeric"
          placeholder="random"
          value={seed}
          onChange={(e) => setSeed(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Size
        <input
          className="w-32 border border-hairline px-3 py-2 font-mono text-sm tabular"
          inputMode="numeric"
          value={size}
          onChange={(e) => setSize(e.target.value)}
        />
      </label>
      <button
        type="submit"
        disabled={busy || !name}
        className="border border-foreground bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
      >
        {busy ? "Generating…" : "Generate"}
      </button>
      <button type="button" onClick={onCancel} className="border border-hairline px-4 py-2 text-sm">
        Cancel
      </button>
      {error && <p role="alert" className="w-full text-sm text-signal">{error}</p>}
    </form>
  );
}

function CreateUploadedForm({
  onDone,
  onCancel,
}: {
  onDone: (dataset: DatasetOut) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const { data, error: apiError } = await api.POST("/datasets", { body: { name, source: "uploaded" } });
    setBusy(false);
    if (!data) {
      setError(apiError && typeof apiError.detail === "string" ? apiError.detail : "Could not create a dataset.");
      return;
    }
    onDone(data);
  }

  return (
    <form onSubmit={submit} className="mt-6 flex flex-wrap items-end gap-4 border border-hairline p-4">
      <label className="flex flex-col gap-1 text-sm">
        Name
        <input
          className="w-56 border border-hairline px-3 py-2 text-sm"
          placeholder="e.g. Q1 books"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <button
        type="submit"
        disabled={busy || !name}
        className="border border-foreground bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50"
      >
        {busy ? "Creating…" : "Create dataset"}
      </button>
      <button type="button" onClick={onCancel} className="border border-hairline px-4 py-2 text-sm">
        Cancel
      </button>
      {error && <p role="alert" className="w-full text-sm text-signal">{error}</p>}
      <p className="w-full text-xs text-muted">
        You&apos;ll add each of the four files (ledger, gateway, settlement, bank) next.
      </p>
    </form>
  );
}

function DatasetDetail({
  datasetId,
  onChanged,
  onClose,
}: {
  datasetId: string;
  onChanged: () => void;
  onClose: () => void;
}) {
  const [dataset, setDataset] = useState<DatasetOut | null>(null);
  const [activeRole, setActiveRole] = useState<string | null>(null);
  const [records, setRecords] = useState<DatasetRecordsOut | null>(null);
  const [offset, setOffset] = useState(0);

  async function refresh(): Promise<void> {
    const { data } = await api.GET("/datasets/{dataset_id}", { params: { path: { dataset_id: datasetId } } });
    setDataset(data ?? null);
  }

  // Keyed by datasetId at the call site below, so this component remounts
  // (resetting activeRole/records for free) whenever the selection changes.
  useEffect(() => {
    api.GET("/datasets/{dataset_id}", { params: { path: { dataset_id: datasetId } } }).then(({ data }) =>
      setDataset(data ?? null)
    );
  }, [datasetId]);

  async function viewRecords(role: string, nextOffset: number): Promise<void> {
    setActiveRole(role);
    setOffset(nextOffset);
    const { data } = await api.GET("/datasets/{dataset_id}/files/{role}/records", {
      params: { path: { dataset_id: datasetId, role }, query: { offset: nextOffset, limit: RECORDS_PAGE_SIZE } },
    });
    setRecords(data ?? null);
  }

  if (!dataset) {
    return <p className="text-sm text-muted">Loading dataset…</p>;
  }

  const filesByRole = Object.fromEntries(dataset.files.map((f) => [f.role, f]));

  return (
    <section className="border border-hairline p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">{dataset.name}</h2>
        <button type="button" onClick={onClose} className="text-xs text-muted underline">
          Close
        </button>
      </div>

      <table className="mt-4 w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-hairline text-left text-muted">
            <th scope="col" className="py-2 font-normal">Role</th>
            <th scope="col" className="py-2 font-normal">Rows</th>
            <th scope="col" className="py-2 font-normal">File</th>
            <th scope="col" className="py-2 font-normal">Actions</th>
          </tr>
        </thead>
        <tbody>
          {ROLES.map(({ value, label }) => {
            const file = filesByRole[value] as DatasetFileOut | undefined;
            return (
              <tr key={value} className="border-b border-hairline align-top">
                <td className="py-2">{label}</td>
                <td className="py-2 font-mono tabular text-xs">
                  {file ? `${file.valid_count}/${file.row_count} valid` : "--"}
                </td>
                <td className="py-2 text-xs text-muted">{file?.raw_filename ?? (file ? "generated" : "not uploaded")}</td>
                <td className="py-2">
                  <div className="flex flex-wrap items-center gap-3">
                    {file && file.valid_count > 0 && (
                      <button
                        type="button"
                        onClick={() => viewRecords(value, 0)}
                        className="text-xs underline"
                      >
                        View processed
                      </button>
                    )}
                    {file?.has_raw && (
                      <a href={`/api/datasets/${datasetId}/files/${value}/raw`} className="text-xs underline">
                        Download raw
                      </a>
                    )}
                    {dataset.source === "uploaded" && (
                      <RoleUploader
                        datasetId={datasetId}
                        role={value}
                        label={file ? "Replace file" : "Upload file"}
                        onSaved={() => {
                          refresh();
                          onChanged();
                        }}
                      />
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {activeRole && records && (
        <div className="mt-6">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-muted">
              {activeRole} -- {records.total} row{records.total === 1 ? "" : "s"}
            </h3>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => viewRecords(activeRole, Math.max(0, offset - RECORDS_PAGE_SIZE))}
                className="border border-hairline px-2 py-1 text-xs disabled:opacity-40"
              >
                Prev
              </button>
              <button
                type="button"
                disabled={offset + records.records.length >= records.total}
                onClick={() => viewRecords(activeRole, offset + RECORDS_PAGE_SIZE)}
                className="border border-hairline px-2 py-1 text-xs disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
          <div className="mt-2 overflow-x-auto">
            <RecordsTable records={records.records} />
          </div>
        </div>
      )}

      {dataset.status === "ready" && (
        <p className="mt-4 text-sm">
          Ready to run. Head to <a href={`/run?dataset=${dataset.id}`} className="underline">Run</a> and pick this
          dataset.
        </p>
      )}
    </section>
  );
}

function RecordsTable({ records }: { records: Record<string, unknown>[] }) {
  if (records.length === 0) {
    return <p className="text-sm text-muted">No rows.</p>;
  }
  const columns = Object.keys(records[0]);
  return (
    <table className="w-full min-w-max border-collapse text-xs">
      <thead>
        <tr className="border-b border-hairline text-left text-muted">
          {columns.map((col) => (
            <th key={col} scope="col" className="whitespace-nowrap py-2 pr-4 font-normal">
              {col}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {records.map((record, i) => (
          <tr key={i} className="border-b border-hairline">
            {columns.map((col) => (
              <td key={col} className="whitespace-nowrap py-2 pr-4 font-mono">
                {String(record[col] ?? "")}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RoleUploader({
  datasetId,
  role,
  label,
  onSaved,
}: {
  datasetId: string;
  role: string;
  label: string;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewOut | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string | null>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runPreview(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
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

  async function confirmAndSave() {
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
    const { error: apiError } = await postForm(`/datasets/${datasetId}/files`, formData);
    setBusy(false);
    if (apiError) {
      setError(apiError.detail ?? "Could not save that file.");
      return;
    }
    setOpen(false);
    setFile(null);
    setPreview(null);
    onSaved();
  }

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="text-xs underline">
        {label}
      </button>
    );
  }

  return (
    <div className="w-full border border-hairline p-3">
      {!preview ? (
        <form onSubmit={runPreview} className="flex flex-wrap items-end gap-3">
          <input
            type="file"
            accept=".csv,.xlsx,.pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-xs"
          />
          <button
            type="submit"
            disabled={!file || busy}
            className="border border-foreground bg-foreground px-3 py-1 text-xs font-medium text-background disabled:opacity-50"
          >
            {busy ? "Reading…" : "Preview mapping"}
          </button>
          <button type="button" onClick={() => setOpen(false)} className="text-xs text-muted underline">
            Cancel
          </button>
        </form>
      ) : (
        <div>
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-hairline text-left text-muted">
                <th scope="col" className="py-1 font-normal">Column</th>
                <th scope="col" className="py-1 font-normal">Sample</th>
                <th scope="col" className="py-1 font-normal">Maps to</th>
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
          <div className="mt-2 flex gap-3">
            <button
              type="button"
              onClick={confirmAndSave}
              disabled={busy}
              className="border border-foreground bg-foreground px-3 py-1 text-xs font-medium text-background disabled:opacity-50"
            >
              {busy ? "Saving…" : "Save to dataset"}
            </button>
            <button
              type="button"
              onClick={() => {
                setPreview(null);
                setFile(null);
                setOpen(false);
              }}
              className="text-xs text-muted underline"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-xs text-signal">{error}</p>}
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
      <td className="py-1 font-mono">{header}</td>
      <td className="py-1 font-mono text-muted">{sample}</td>
      <td className="py-1">
        <select
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value || null)}
          className="border border-hairline px-1 py-0.5 text-xs"
        >
          <option value="">(ignore)</option>
          {canonicalFields.map((field) => (
            <option key={field} value={field}>
              {field}
            </option>
          ))}
        </select>
        {mapping && <span className="ml-2 text-[10px] text-muted">{mapping.confidence.toFixed(2)}</span>}
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
