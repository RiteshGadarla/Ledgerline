"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { EmptyState, Loading, Modal, PanelHead, Surface } from "@/components/Surface";
import { RequireAuth } from "@/components/RequireAuth";
import { UserBadge } from "@/components/UserBadge";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/client";
import { formatTimestamp } from "@/lib/time";

type DatasetOut = components["schemas"]["DatasetOut"];
type DatasetFileOut = components["schemas"]["DatasetFileOut"];
type DatasetRecordsOut = components["schemas"]["DatasetRecordsOut"];
type PreviewOut = components["schemas"]["PreviewOut"];
type FieldMappingOut = components["schemas"]["FieldMappingOut"];

const ROLES: { value: string; label: string; hint: string }[] = [
  { value: "ledger", label: "Ledger", hint: "invoices you raised" },
  { value: "gateway", label: "Gateway", hint: "payments captured" },
  { value: "settlement", label: "Settlement", hint: "payouts made to you" },
  { value: "bank", label: "Bank statement", hint: "credits that landed" },
];

const RECORDS_PAGE_SIZE = 20;

const DEFAULT_SEED = "42";

function isNameTaken(existingNames: string[], name: string): boolean {
  const candidate = name.trim().toLowerCase();
  return candidate !== "" && existingNames.some((n) => n.trim().toLowerCase() === candidate);
}

/** Synthetic-1, then Synthetic-2, and so on: the lowest number not already taken. */
function nextSyntheticName(existingNames: string[]): string {
  const taken = new Set(existingNames.map((n) => n.trim().toLowerCase()));
  let n = 1;
  while (taken.has(`synthetic-${n}`)) n += 1;
  return `Synthetic-${n}`;
}

// FormData uploads don't fit openapi-fetch's typed body shape cleanly (the
// generated type for a multipart file field is `string`, not `File`), so
// these go through plain fetch against the same proxy route and are cast to
// the response types the generated schema already describes.
async function postForm<T>(
  path: string,
  formData: FormData,
): Promise<{ data?: T; error?: { detail?: string } }> {
  const response = await fetch(`/api${path}`, { method: "POST", body: formData });
  const body = await response.json();
  return response.ok ? { data: body as T } : { error: body as { detail?: string } };
}

function DataSurface() {
  const [datasets, setDatasets] = useState<DatasetOut[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState<"generate" | "upload" | null>(null);
  const [deleting, setDeleting] = useState<DatasetOut | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function confirmDelete(dataset: DatasetOut): Promise<void> {
    setDeleteBusy(true);
    setDeleteError(null);
    const { error } = await api.DELETE("/datasets/{dataset_id}", {
      params: { path: { dataset_id: dataset.id } },
    });
    setDeleteBusy(false);
    if (error) {
      // FastAPI's 422 body carries a list of field errors rather than a
      // sentence, so only a real string is shown as one.
      setDeleteError(
        typeof error.detail === "string" ? error.detail : "Could not delete that dataset.",
      );
      return;
    }
    if (selectedId === dataset.id) setSelectedId(null);
    setDeleting(null);
    await refreshDatasets();
  }

  async function refreshDatasets(): Promise<void> {
    const { data } = await api.GET("/datasets");
    setDatasets(data ?? []);
  }

  useEffect(() => {
    api.GET("/datasets").then(({ data }) => setDatasets(data ?? []));
  }, []);

  const existingNames = (datasets ?? []).map((d) => d.name);

  return (
    <Surface
      crumb="Console"
      title={<span className="text-[16.5px] font-semibold tracking-[-0.015em]">Data</span>}
      tools={<UserBadge />}
      strip={[
        { label: "READY", tone: "var(--readout-hi)" },
        { label: "PARSER", value: "polars · pdfplumber" },
        { label: "MAPPING", value: "user-confirmed" },
      ]}
    >
      <p className="max-w-[80ch] text-[14.5px] leading-relaxed text-muted">
        A dataset is a reusable set of ledger, gateway, settlement and bank files. Generate a
        synthetic one to see the engine work, or upload your own, and either becomes something you can
        close the books against.
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <ChoiceCard
          title="Generate a synthetic dataset"
          body="A seeded corpus with a known truth file, so precision and recall are meaningful. The fastest way to see a full run end to end."
          cta="Generate"
          onClick={() => setCreating("generate")}
          primary
        />
        <ChoiceCard
          title="Upload your own files"
          body="CSV, XLSX or PDF per role. Columns are mapped to canonical fields on upload; you can review and override the mapping per file afterwards."
          cta="Upload"
          onClick={() => setCreating("upload")}
        />
      </div>

      {creating === "generate" && (
        <Modal title="Generate a synthetic dataset" onClose={() => setCreating(null)}>
          <GenerateForm
            existingNames={existingNames}
            onDone={async (dataset) => {
              await refreshDatasets();
              setSelectedId(dataset.id);
              setCreating(null);
            }}
            onCancel={() => setCreating(null)}
          />
        </Modal>
      )}

      {creating === "upload" && (
        <Modal title="Upload your own files" onClose={() => setCreating(null)}>
          <UploadDatasetForm
            existingNames={existingNames}
            onDone={async (datasetId) => {
              await refreshDatasets();
              setSelectedId(datasetId);
              setCreating(null);
            }}
            onCancel={() => setCreating(null)}
          />
        </Modal>
      )}

      <section className="panel">
        <PanelHead
          legend="Your datasets"
          note={datasets && datasets.length > 0 ? `${datasets.length} TOTAL` : undefined}
        />

        <div className="overflow-hidden">
          {datasets === null ? (
            <div className="px-5">
              <Loading />
            </div>
          ) : datasets.length === 0 ? (
            <EmptyState
              title="No datasets yet"
              body="Generate a synthetic corpus or upload your own files to get started."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="grid-table">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Source</th>
                    <th scope="col">Files</th>
                    <th scope="col">Status</th>
                    <th scope="col">Created</th>
                    <th scope="col">
                      <span className="sr-only">Delete</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {datasets.map((dataset) => {
                    const selected = dataset.id === selectedId;
                    return (
                      <tr
                        key={dataset.id}
                        onClick={() => setSelectedId(selected ? null : dataset.id)}
                        className={"row-interactive " + (selected ? "row-selected" : "")}
                      >
                        <td className="font-medium">{dataset.name}</td>
                        <td className="text-muted">{dataset.source}</td>
                        <td className="font-mono text-xs tabular text-muted">
                          {dataset.files.filter((f) => f.valid_count > 0).length}/4
                        </td>
                        <td>
                          <DatasetStatusPill status={dataset.status} />
                        </td>
                        <td className="text-muted tabular">
                          {formatTimestamp(dataset.created_at)}
                        </td>
                        <td className="w-px">
                          {/* The row itself selects; this must not, or asking
                              to delete would open the thing being deleted. */}
                          <button
                            type="button"
                            aria-label={`Delete ${dataset.name}`}
                            title={`Delete ${dataset.name}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              setDeleteError(null);
                              setDeleting(dataset);
                            }}
                            className="btn btn-icon !h-7 !min-h-7 !w-7 !border-transparent !bg-transparent text-faint hover:!border-signal hover:text-signal"
                          >
                            <svg
                              width="15"
                              height="15"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="1.7"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              aria-hidden
                            >
                              <path d="M4 7h16" />
                              <path d="M10 11v6M14 11v6" />
                              <path d="M6 7l1 13h10l1-13" />
                              <path d="M9 7V4h6v3" />
                            </svg>
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {deleting && (
        <Modal
          title="Delete dataset"
          onClose={() => (deleteBusy ? undefined : setDeleting(null))}
          footer={
            <>
              <button
                type="button"
                onClick={() => setDeleting(null)}
                disabled={deleteBusy}
                className="btn btn-sm"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmDelete(deleting)}
                disabled={deleteBusy}
                className="btn btn-sm !border-signal !bg-signal !text-white hover:!bg-[color:var(--signal)]"
              >
                {deleteBusy ? "Deleting…" : "Delete permanently"}
              </button>
            </>
          }
        >
          <div className="flex flex-col gap-3">
            <p className="text-sm">
              Delete <span className="font-medium">{deleting.name}</span>, its four files, and
              everything derived from them?
            </p>
            {/* Said before it happens, not after: a run cites records by id,
                and once the dataset is gone its scoreboard cannot be
                re-derived from anything. */}
            <ul className="flex flex-col gap-1.5 border-l-2 border-signal/60 pl-3 text-[13px] leading-snug text-muted">
              <li>
                <span className="mono text-[12px] text-signal">
                  {deleting.files.filter((f) => f.valid_count > 0).length} file(s)
                </span>{" "}
                and every record parsed from them.
              </li>
              <li>
                <span className="mono text-[12px] text-signal">
                  {deleting.run_count} run(s)
                </span>{" "}
                made from this dataset, with their scoreboards, exceptions and decisions. A run whose
                records no longer exist cannot be re-derived, so it goes too.
              </li>
              <li>The original uploads. Download them first if you need them.</li>
            </ul>
            <p className="text-[13px] text-faint">This cannot be undone.</p>
            {deleteError && (
              <p role="alert" className="text-[13px] text-signal">
                {deleteError}
              </p>
            )}
          </div>
        </Modal>
      )}

      {selectedId && (
        <DatasetDetail
          key={selectedId}
          datasetId={selectedId}
          onChanged={refreshDatasets}
          onClose={() => setSelectedId(null)}
        />
      )}
    </Surface>
  );
}

function ChoiceCard({
  title,
  body,
  cta,
  onClick,
  primary = false,
}: {
  title: string;
  body: string;
  cta: string;
  onClick: () => void;
  primary?: boolean;
}) {
  return (
    <div className="panel flex flex-col gap-3 p-4">
      <span className="legend legend-hi">{title}</span>
      <p className="flex-1 text-[14px] leading-relaxed text-muted">{body}</p>
      <button
        type="button"
        onClick={onClick}
        className={"btn self-start " + (primary ? "btn-primary" : "")}
      >
        {cta}
      </button>
    </div>
  );
}

function DatasetStatusPill({ status }: { status: string }) {
  const ready = status === "ready";
  return (
    <span className={"chip " + (ready ? "chip-tied" : "")}>
      <span aria-hidden className="dot" />
      {ready ? "Ready" : "Incomplete"}
    </span>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="label">
        {label}
        {hint && <span className="ml-1.5 font-normal text-faint">{hint}</span>}
      </span>
      {children}
    </div>
  );
}

function GenerateForm({
  existingNames,
  onDone,
  onCancel,
}: {
  existingNames: string[];
  onDone: (dataset: DatasetOut) => void;
  onCancel: () => void;
}) {
  // Seeded once from the names that existed when the modal opened; the field
  // stays editable, so a later keystroke isn't fighting a recomputed default.
  const [name, setName] = useState(() => nextSyntheticName(existingNames));
  const [seed, setSeed] = useState(DEFAULT_SEED);
  const [size, setSize] = useState("150");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const taken = isNameTaken(existingNames, name);
  const seedBlank = seed.trim() === "";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const { data, error: apiError } = await api.POST("/datasets", {
      body: {
        name,
        source: "generated",
        seed: Number(seed),
        size: size ? Number(size) : undefined,
      },
    });
    setBusy(false);
    if (!data) {
      setError(
        apiError && typeof apiError.detail === "string"
          ? apiError.detail
          : "Could not generate a dataset.",
      );
      return;
    }
    onDone(data);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5">
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Name">
          <input
            className="field"
            value={name}
            aria-invalid={taken || undefined}
            onChange={(e) => setName(e.target.value)}
          />
          {taken && <span className="text-[12.5px] text-signal">That name is already used.</span>}
        </Field>
        <Field label="Seed">
          <input
            className="field font-mono tabular"
            inputMode="numeric"
            aria-invalid={seedBlank || undefined}
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
          />
          {seedBlank && <span className="text-[12.5px] text-signal">Seed is required.</span>}
        </Field>
        <Field label="Size">
          <input
            className="field font-mono tabular"
            inputMode="numeric"
            value={size}
            onChange={(e) => setSize(e.target.value)}
          />
        </Field>
      </div>

      <p className="text-xs leading-relaxed text-faint">
        A fixed seed makes the corpus reproducible: the same seed and size generate the same
        records, which is what lets a run&apos;s output hash be compared against a previous one.
      </p>

      <div className="flex gap-2.5">
        <button
          type="submit"
          disabled={busy || !name.trim() || taken || seedBlank}
          className="btn btn-primary"
        >
          {busy ? "Generating…" : "Generate dataset"}
        </button>
        <button type="button" onClick={onCancel} className="btn btn-ghost">
          Cancel
        </button>
      </div>

      {error && (
        <p
          role="alert"
          className="rounded-lg border border-signal/50 bg-signal-bg px-3 py-2 text-sm text-signal"
        >
          {error}
        </p>
      )}
    </form>
  );
}

type RowError = { row_number: number; reason: string };

type RoleUploadOutcome =
  | {
      ok: true;
      valid_count: number;
      total_rows: number;
      error_count: number;
      notes: string[];
      errors: RowError[];
    }
  | { ok: false; error: string };

/** Rejections collapse hard: a whole file usually fails for one reason, and
 *  fifty-six copies of that reason is not a report. Group by reason, count
 *  the rows, and lead with the one that cost the most. */
function groupReasons(errors: RowError[]): { reason: string; count: number; firstRow: number }[] {
  const byReason = new Map<string, { reason: string; count: number; firstRow: number }>();
  for (const error of errors) {
    const seen = byReason.get(error.reason);
    if (seen) seen.count += 1;
    else byReason.set(error.reason, { reason: error.reason, count: 1, firstRow: error.row_number });
  }
  return [...byReason.values()].sort((a, b) => b.count - a.count);
}

/**
 * What the parser had to do to read the file, said out loud.
 *
 * Real exports arrive with a letterhead above the columns, a totals line at
 * the bottom and a spacer column down the middle, and the parser repairs all
 * of it. A repair nobody is told about is indistinguishable from a bug, and
 * a reconciliation that quietly skipped forty rows still balances, and still
 * lies. So every repair is reported, every rejected row is counted, and the
 * count of what was saved is stated against the count of what was read.
 */
function RoleUploadResult({ outcome }: { outcome: RoleUploadOutcome }) {
  if (!outcome.ok) {
    return <span className="text-xs text-signal">{outcome.error}</span>;
  }
  const reasons = groupReasons(outcome.errors);
  const shown = reasons.slice(0, 3);
  const remaining = reasons.length - shown.length;
  return (
    <div className="flex flex-col gap-1">
      <span className={"text-xs " + (outcome.error_count > 0 ? "text-caution" : "text-positive")}>
        {outcome.valid_count}/{outcome.total_rows} rows saved
        {outcome.error_count > 0 && ` · ${outcome.error_count} rejected`}
      </span>
      {outcome.notes.map((note) => (
        <span key={note} className="text-[12.5px] leading-snug text-faint">
          {note}
        </span>
      ))}
      {/* Why they were rejected, not just how many. A count alone sends the
          uploader back to a file of 56 good-looking rows with nothing to go
          on; the reason names the column and the value that failed. */}
      {shown.length > 0 && (
        <ul className="flex flex-col gap-1 border-l-2 border-caution/60 pl-2.5">
          {shown.map((item) => (
            <li key={item.reason} className="text-[12.5px] leading-snug text-muted">
              <span className="mono text-[11.5px] text-caution">
                {item.count} row{item.count === 1 ? "" : "s"}
              </span>{" "}
              {item.reason}
              <span className="text-faint"> (first at row {item.firstRow})</span>
            </li>
          ))}
          {remaining > 0 && (
            <li className="text-[12.5px] text-faint">and {remaining} other reason(s)</li>
          )}
        </ul>
      )}
    </div>
  );
}

function UploadDatasetForm({
  existingNames,
  onDone,
  onCancel,
}: {
  existingNames: string[];
  onDone: (datasetId: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [files, setFiles] = useState<Record<string, File | null>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, RoleUploadOutcome> | null>(null);

  const hasAnyFile = ROLES.some((r) => files[r.value]);
  const taken = isNameTaken(existingNames, name);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setResults(null);

    const { data: dataset, error: apiError } = await api.POST("/datasets", {
      body: { name, source: "uploaded" },
    });
    if (!dataset) {
      setBusy(false);
      setError(
        apiError && typeof apiError.detail === "string"
          ? apiError.detail
          : "Could not create a dataset.",
      );
      return;
    }

    const outcomes: Record<string, RoleUploadOutcome> = {};
    for (const { value: role } of ROLES) {
      const file = files[role];
      if (!file) continue;

      const previewForm = new FormData();
      previewForm.set("role", role);
      previewForm.set("file", file);
      const { data: preview, error: previewError } = await postForm<PreviewOut>(
        "/data/preview",
        previewForm,
      );
      if (!preview) {
        outcomes[role] = { ok: false, error: previewError?.detail ?? "Could not read that file." };
        continue;
      }

      const mapping = preview.mapping.map((m) => ({
        source_header: m.source_header,
        canonical_field: m.canonical_field,
        confidence: m.confidence,
      }));
      const saveForm = new FormData();
      saveForm.set("role", role);
      saveForm.set("mapping", JSON.stringify(mapping));
      saveForm.set("file", file);
      const { data: saved, error: saveError } = await postForm<{
        valid_count: number;
        total_rows: number;
        error_count: number;
        notes: string[];
        errors: RowError[];
      }>(`/datasets/${dataset.id}/files`, saveForm);
      outcomes[role] = saved
        ? {
            ok: true,
            valid_count: saved.valid_count,
            total_rows: saved.total_rows,
            error_count: saved.error_count ?? 0,
            notes: saved.notes ?? [],
            errors: saved.errors ?? [],
          }
        : { ok: false, error: saveError?.detail ?? "Could not save that file." };
    }

    setResults(outcomes);
    setBusy(false);
    onDone(dataset.id);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5">
      <Field label="Dataset name" hint="unique">
        <input
          className="field sm:max-w-xs"
          placeholder="e.g. Q1 books"
          value={name}
          aria-invalid={taken || undefined}
          onChange={(e) => setName(e.target.value)}
        />
        {taken && <span className="text-[12.5px] text-signal">That name is already used.</span>}
      </Field>

      <div className="grid gap-3 sm:grid-cols-2">
        {ROLES.map(({ value, label, hint }) => (
          <div key={value} className="card-flush flex flex-col gap-2 p-4">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-medium">{label}</span>
              <span className="text-[12.5px] text-faint">{hint}</span>
            </div>
            <input
              type="file"
              accept=".csv,.xlsx,.pdf"
              aria-label={`${label} file`}
              onChange={(e) =>
                setFiles((prev) => ({ ...prev, [value]: e.target.files?.[0] ?? null }))
              }
              className="field field-file py-2 text-xs"
            />
            {results?.[value] && <RoleUploadResult outcome={results[value]} />}
          </div>
        ))}
      </div>

      <div className="flex gap-2.5">
        <button
          type="submit"
          disabled={busy || !name.trim() || taken || !hasAnyFile}
          className="btn btn-primary"
        >
          {busy ? "Uploading…" : "Upload dataset"}
        </button>
        <button type="button" onClick={onCancel} className="btn btn-ghost">
          Cancel
        </button>
      </div>

      {error && (
        <p
          role="alert"
          className="rounded-lg border border-signal/50 bg-signal-bg px-3 py-2 text-sm text-signal"
        >
          {error}
        </p>
      )}
      <p className="text-xs text-faint">
        You can add or replace any of these files later from the dataset&apos;s own panel below.
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
  const [loadingRecords, setLoadingRecords] = useState(false);
  const [offset, setOffset] = useState(0);
  const [pageSize, setPageSize] = useState(RECORDS_PAGE_SIZE);
  const [replacing, setReplacing] = useState(false);
  const [confirmingFileDelete, setConfirmingFileDelete] = useState(false);
  const [fileDeleteBusy, setFileDeleteBusy] = useState(false);

  async function refresh(): Promise<void> {
    const { data } = await api.GET("/datasets/{dataset_id}", {
      params: { path: { dataset_id: datasetId } },
    });
    setDataset(data ?? null);
  }

  async function loadRecords(role: string, nextOffset: number, limit: number): Promise<void> {
    setActiveRole(role);
    setOffset(nextOffset);
    setLoadingRecords(true);
    const { data } = await api.GET("/datasets/{dataset_id}/files/{role}/records", {
      params: { path: { dataset_id: datasetId, role }, query: { offset: nextOffset, limit } },
    });
    setRecords(data ?? null);
    setLoadingRecords(false);
  }

  // Keyed by datasetId at the call site, so this component remounts whenever
  // the selection changes; the first role that actually has rows opens on its
  // own, so the viewer never lands on an empty table.
  useEffect(() => {
    let cancelled = false;
    api
      .GET("/datasets/{dataset_id}", { params: { path: { dataset_id: datasetId } } })
      .then(({ data }) => {
        if (cancelled || !data) return;
        setDataset(data);
        const first = data.files.find((f) => f.valid_count > 0);
        if (first) void loadRecords(first.role, 0, RECORDS_PAGE_SIZE);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetId]);

  const filesByRole: Record<string, DatasetFileOut | undefined> = Object.fromEntries(
    (dataset?.files ?? []).map((f) => [f.role, f]),
  );
  const activeFile = activeRole ? filesByRole[activeRole] : undefined;
  const canReplace = dataset?.source === "uploaded";
  const shownFrom = records && records.total > 0 ? offset + 1 : 0;
  const shownTo = records ? offset + records.records.length : 0;

  return (
    <Modal
      size="lg"
      height="tall"
      padded={false}
      ariaLabel={dataset ? `Dataset ${dataset.name}` : "Dataset"}
      onClose={onClose}
      title={
        <span className="flex min-w-0 items-baseline gap-2.5">
          <span className="legend">Dataset</span>
          <span aria-hidden className="text-faint">
            /
          </span>
          <span className="truncate text-[16.5px] font-semibold tracking-[-0.015em]">
            {dataset?.name ?? "Loading…"}
          </span>
        </span>
      }
      meta={
        dataset && (
          <span className="flex flex-wrap items-center gap-1.5">
            <DatasetStatusPill status={dataset.status} />
            <span className="chip">{dataset.source}</span>
            {dataset.seed !== null && <span className="chip">seed {dataset.seed}</span>}
            {dataset.size !== null && <span className="chip">size {dataset.size}</span>}
          </span>
        )
      }
      footer={
        <>
          <span className="mono text-[12.5px] text-faint tabular">
            {records
              ? `${shownFrom}–${shownTo} of ${records.total} row${records.total === 1 ? "" : "s"}`
              : "No rows loaded"}
          </span>
          {activeRole && records && records.total > pageSize && (
            <span className="flex items-center gap-1.5">
              <button
                type="button"
                disabled={offset === 0 || loadingRecords}
                onClick={() => loadRecords(activeRole, Math.max(0, offset - pageSize), pageSize)}
                className="btn btn-ghost btn-sm"
              >
                Prev
              </button>
              <button
                type="button"
                disabled={shownTo >= records.total || loadingRecords}
                onClick={() => loadRecords(activeRole, offset + pageSize, pageSize)}
                className="btn btn-ghost btn-sm"
              >
                Next
              </button>
            </span>
          )}
          {activeRole && (
            <label className="flex items-center gap-1.5">
              <span className="legend">Rows</span>
              <select
                aria-label="Rows per page"
                value={pageSize}
                onChange={(e) => {
                  const next = Number(e.target.value);
                  setPageSize(next);
                  void loadRecords(activeRole, 0, next);
                }}
                className="field w-[4.5rem] py-1 text-xs"
              >
                {[20, 50, 100].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
          )}
          <span className="ml-auto flex items-center gap-2">
            {dataset?.status === "ready" && (
              <Link href={`/run?dataset=${dataset.id}`} className="btn btn-primary btn-sm">
                Close the books
              </Link>
            )}
          </span>
        </>
      }
    >
      {!dataset ? (
        <div className="px-5 pt-4">
          <Loading label="Loading dataset…" />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          {/* Role tabs: the same segmented control the run channels use. */}
          <nav
            aria-label="Dataset roles"
            className="flex shrink-0 items-stretch overflow-x-auto border-b border-hairline bg-sunk"
          >
            {ROLES.map(({ value, label }, i) => {
              const file = filesByRole[value];
              const empty = !file || file.valid_count === 0;
              const active = value === activeRole;
              return (
                <button
                  key={value}
                  type="button"
                  aria-current={active ? "true" : undefined}
                  onClick={() => {
                    setReplacing(false);
                    void loadRecords(value, 0, pageSize);
                  }}
                  className={
                    "relative flex min-h-[42px] items-center gap-2 whitespace-nowrap border-r border-hairline px-4 text-[12.5px] font-semibold uppercase tracking-[0.1em] transition-colors " +
                    (i === 0 ? "border-l-0 " : "") +
                    (active ? "bg-surface text-foreground" : "text-muted hover:text-foreground")
                  }
                >
                  {active && (
                    <span aria-hidden className="absolute inset-x-0 -top-px h-0.5 bg-readout-hi" />
                  )}
                  {label}
                  <span
                    className={
                      "mono rounded-sm px-1.5 py-px text-[11.5px] tracking-normal " +
                      (empty ? "text-faint" : "bg-positive-bg text-positive")
                    }
                  >
                    {empty ? "-" : file.valid_count}
                  </span>
                </button>
              );
            })}
          </nav>

          {/* Per-role toolbar: where this role's rows came from, and what you
              can do to them. */}
          <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-hairline px-5 py-2.5">
            <span className="mono truncate text-[12.5px] text-muted">
              {activeFile?.raw_filename ??
                (activeFile ? "generated in place" : "no file for this role")}
            </span>
            {activeFile && (
              <span className="mono text-[12.5px] text-faint tabular">
                {activeFile.valid_count}/{activeFile.row_count} rows valid
              </span>
            )}
            <span className="ml-auto flex items-center gap-2">
              {activeFile?.has_raw && (
                <a
                  href={`/api/datasets/${datasetId}/files/${activeRole}/raw`}
                  className="btn btn-sm"
                >
                  Download raw
                </a>
              )}
              {canReplace && activeRole && !replacing && (
                <button type="button" onClick={() => setReplacing(true)} className="btn btn-sm">
                  {activeFile ? "Replace file" : "Upload file"}
                </button>
              )}
              {canReplace && activeRole && activeFile && !replacing && (
                <button
                  type="button"
                  onClick={() => setConfirmingFileDelete(true)}
                  className="btn btn-sm !border-signal/50 !text-signal hover:!bg-signal-bg"
                >
                  Delete file
                </button>
              )}
            </span>
          </div>

          {confirmingFileDelete && activeRole && (
            <div className="shrink-0 border-b border-hairline bg-signal-bg px-5 py-3">
              <p className="text-[13.5px] font-medium text-signal">
                Delete the {activeRole} file?
              </p>
              <p className="mt-1 text-[13px] leading-snug text-muted">
                Its rows and the original upload go. The dataset itself stays, one role lighter, and
                drops out of ready because a run needs all four. Runs already scored against this
                file are left alone -- they were measured on the records as they stood.
              </p>
              <div className="mt-2.5 flex gap-2">
                <button
                  type="button"
                  disabled={fileDeleteBusy}
                  onClick={async () => {
                    setFileDeleteBusy(true);
                    await api.DELETE("/datasets/{dataset_id}/files/{role}", {
                      params: { path: { dataset_id: datasetId, role: activeRole } },
                    });
                    setFileDeleteBusy(false);
                    setConfirmingFileDelete(false);
                    refresh();
                    onChanged();
                    void loadRecords(activeRole, 0, pageSize);
                  }}
                  className="btn btn-sm !border-signal !bg-signal !text-white"
                >
                  {fileDeleteBusy ? "Deleting…" : "Delete file"}
                </button>
                <button
                  type="button"
                  disabled={fileDeleteBusy}
                  onClick={() => setConfirmingFileDelete(false)}
                  className="btn btn-sm"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Capped, with its own scroll: a wide file's mapping table must not
              push the records out of the dialog. */}
          {canReplace && activeRole && replacing && (
            <div className="max-h-[55%] shrink-0 overflow-auto border-b border-hairline p-5">
              <RoleUploader
                key={activeRole}
                datasetId={datasetId}
                role={activeRole}
                onCancel={() => setReplacing(false)}
                onSaved={() => {
                  setReplacing(false);
                  refresh();
                  onChanged();
                  void loadRecords(activeRole, 0, pageSize);
                }}
              />
            </div>
          )}

          <div className="min-h-0 flex-1 overflow-auto">
            {loadingRecords ? (
              <div className="px-5">
                <Loading label="Reading rows…" />
              </div>
            ) : !activeRole ? (
              <EmptyState
                title="Nothing to view yet"
                body="This dataset has no processed rows in any role. Upload a file to fill one in."
              />
            ) : !records || records.records.length === 0 ? (
              // A file that was read and wholly rejected is not the same
              // thing as a role nobody has filled in, and saying "nothing has
              // been parsed" about 56 parsed rows sends the uploader looking
              // for a file that is already here. Name which of the two it is.
              activeFile && activeFile.row_count > 0 ? (
                <EmptyState
                  title={`All ${activeFile.row_count} rows were rejected`}
                  body="The file was read and its columns were mapped, but no row survived validation, so this role holds nothing the engine can run on. Replace the file to see the reason for each rejected row."
                  action={
                    canReplace && !replacing ? (
                      <button type="button" onClick={() => setReplacing(true)} className="btn btn-sm">
                        Replace file
                      </button>
                    ) : undefined
                  }
                />
              ) : (
                <EmptyState
                  title="No rows in this role"
                  body="Nothing has been parsed and validated here yet."
                />
              )
            ) : (
              <RecordsTable records={records.records} startIndex={offset} />
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

function RecordsTable({
  records,
  startIndex,
}: {
  records: Record<string, unknown>[];
  startIndex: number;
}) {
  const columns = Object.keys(records[0]);
  return (
    <table className="grid-table min-w-max">
      <thead>
        <tr>
          {/* Sticky over an opaque --sunk header, so the column names stay
              readable however far down a long role you scroll. */}
          <th scope="col" className="sticky top-0 z-10 text-right">
            #
          </th>
          {columns.map((col) => (
            <th key={col} scope="col" className="sticky top-0 z-10">
              {col}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {records.map((record, i) => (
          <tr key={i} className="row-interactive">
            <td className="mono text-right text-[12.5px] text-faint tabular">{startIndex + i + 1}</td>
            {columns.map((col) => (
              <td key={col} className="whitespace-nowrap font-mono text-xs">
                {String(record[col] ?? "")}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The mapping-confirming uploader for one role. The caller decides when it
 *  is on screen; this only owns the file, its preview and the overrides. */
function RoleUploader({
  datasetId,
  role,
  onCancel,
  onSaved,
}: {
  datasetId: string;
  role: string;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewOut | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string | null>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Held only for the case that has nothing to show for itself: a save that
  // stored no rows closes nothing and reports why instead.
  const [rejected, setRejected] = useState<RoleUploadOutcome | null>(null);

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
    const { data: saved, error: apiError } = await postForm<{
      valid_count: number;
      total_rows: number;
      error_count: number;
      notes: string[];
      errors: RowError[];
    }>(`/datasets/${datasetId}/files`, formData);
    setBusy(false);
    if (apiError || !saved) {
      setError(apiError?.detail ?? "Could not save that file.");
      return;
    }
    // A file every row of which was rejected is a failed upload wearing a
    // success's clothes: HTTP 201, nothing stored. Keep the panel open on
    // the reasons rather than dismissing it onto an empty table.
    if (saved.valid_count === 0) {
      setRejected({
        ok: true,
        valid_count: saved.valid_count,
        total_rows: saved.total_rows,
        error_count: saved.error_count ?? 0,
        notes: saved.notes ?? [],
        errors: saved.errors ?? [],
      });
      return;
    }
    setFile(null);
    setPreview(null);
    setRejected(null);
    onSaved();
  }

  return (
    <div className="card-flush w-full p-4">
      {rejected && (
        <div
          role="alert"
          className="mb-3 rounded-[3px] border border-caution/50 bg-caution-bg px-3 py-2.5"
        >
          <p className="text-[13px] font-medium text-caution">
            Nothing was stored: every row was rejected.
          </p>
          <div className="mt-1.5">
            <RoleUploadResult outcome={rejected} />
          </div>
        </div>
      )}
      {!preview ? (
        <form onSubmit={runPreview} className="flex flex-wrap items-center gap-3">
          <input
            type="file"
            accept=".csv,.xlsx,.pdf"
            aria-label={`${role} file`}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="field field-file max-w-xs py-2 text-xs"
          />
          <button type="submit" disabled={!file || busy} className="btn btn-primary btn-sm">
            {busy ? "Reading…" : "Preview mapping"}
          </button>
          <button type="button" onClick={onCancel} className="btn btn-sm">
            Cancel
          </button>
        </form>
      ) : (
        <div>
          <p className="legend mb-3">Column mapping</p>
          <div className="overflow-x-auto">
            <table className="grid-table">
              <thead>
                <tr>
                  <th scope="col">Column</th>
                  <th scope="col">Sample</th>
                  <th scope="col">Maps to</th>
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
          </div>
          <div className="mt-4 flex gap-2.5">
            <button
              type="button"
              onClick={confirmAndSave}
              disabled={busy}
              className="btn btn-primary btn-sm"
            >
              {busy ? "Saving…" : "Save to dataset"}
            </button>
            <button
              type="button"
              onClick={() => {
                setPreview(null);
                setFile(null);
                onCancel();
              }}
              className="btn btn-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-3 text-xs text-signal">
          {error}
        </p>
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
    <tr>
      <td className="font-mono text-xs">{header}</td>
      <td className="font-mono text-xs text-muted">{sample}</td>
      <td>
        <div className="flex items-center gap-2">
          <select
            value={value ?? ""}
            onChange={(e) => onChange(e.target.value || null)}
            aria-label={`Canonical field for ${header}`}
            className="field w-48 py-1.5 text-xs"
          >
            <option value="">(ignore)</option>
            {canonicalFields.map((field) => (
              <option key={field} value={field}>
                {field}
              </option>
            ))}
          </select>
          {mapping && (
            <span className="mono text-[11.5px] text-faint">{mapping.confidence.toFixed(2)}</span>
          )}
        </div>
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
