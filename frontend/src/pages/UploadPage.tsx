import { CheckCircle2, FileSpreadsheet, Loader2, UploadCloud, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Note } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { fmtDate, fmtInt, humanize } from "../lib/format";
import { ApiError, api, uploadCsv } from "../services/api";
import type { Batch, UploadResult } from "../types/api";

const STAGES = ["preprocessing", "embedding", "clustering", "classification", "labeling", "deduplication", "scoring"];

function BatchProgress({ batch }: { batch: Batch }) {
  const done = batch.status === "PROCESSED";
  const failed = batch.status === "FAILED";
  const current = STAGES.indexOf(batch.stage ?? "");
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        {done ? <CheckCircle2 className="h-4 w-4 text-[var(--good)]" /> : failed ? <XCircle className="h-4 w-4 text-[var(--sev-critical)]" />
          : <Loader2 className="h-4 w-4 animate-spin text-accent" />}
        {done ? "Processing complete" : failed ? "Processing failed" : batch.status === "UPLOADED" ? "Queued" : `Processing: ${humanize(batch.stage)}`}
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-surface-2">
        <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${Math.round((done ? 1 : batch.progress) * 100)}%` }} />
      </div>
      <ol className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
        {STAGES.map((s, i) => (
          <li key={s} className={done || i < current ? "text-ink" : i === current ? "font-medium text-accent-ink" : "text-muted"}>
            {done || i < current ? "✓ " : ""}{humanize(s)}
          </li>
        ))}
      </ol>
      {failed && <Note tone="warn">{batch.error_message}</Note>}
    </div>
  );
}

export function UploadPage() {
  const [file, setFile] = useState<File>();
  const [drag, setDrag] = useState(false);
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<UploadResult>();
  const [batch, setBatch] = useState<Batch>();
  const [error, setError] = useState<{ message: string; batchId?: string }>();
  const inputRef = useRef<HTMLInputElement>(null);
  const history = useApi(() => api.batches().then((r) => r.data), [batch?.status]);

  // Poll batch status until it finishes
  useEffect(() => {
    if (!result) return;
    let stop = false;
    const poll = async () => {
      try {
        const b = (await api.batch(result.batch_id)).data;
        if (stop) return;
        setBatch(b);
        if (b.status === "PROCESSING" || b.status === "UPLOADED") setTimeout(poll, 800);
      } catch (e) {
        if (!stop) setError({ message: (e as Error).message });
      }
    };
    poll();
    return () => { stop = true; };
  }, [result]);

  const start = useCallback(async (f: File) => {
    setFile(f);
    setResult(undefined);
    setBatch(undefined);
    setError(undefined);
    setProgress(0);
    setUploading(true);
    try {
      const res = await uploadCsv(f, setProgress);
      setResult(res.data);
    } catch (e) {
      const err = e as ApiError;
      const details = err.details as { batch_id?: string } | undefined;
      setError({ message: err.message, batchId: details?.batch_id });
    } finally {
      setUploading(false);
    }
  }, []);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDrag(false);
    const f = e.dataTransfer.files?.[0];
    if (f) start(f);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Upload complaints</h1>
        <p className="text-sm text-muted">
          CSV with a required <code className="rounded bg-surface-2 px-1">complaint_text</code> column. Optional: customer_id, source, timestamp,
          country, state, age_group, student_status, income_bracket.
        </p>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
        className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors ${
          drag ? "border-accent bg-accent-wash" : "border-line bg-surface hover:bg-surface-2"
        }`}
      >
        <UploadCloud className="h-8 w-8 text-accent" />
        <div className="text-sm font-medium">Drop a CSV file here, or click to browse</div>
        <div className="text-xs text-muted">Max 20 MB · identical files are detected by SHA-256 and rejected</div>
        <input ref={inputRef} type="file" accept=".csv,text/csv" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) start(f); e.target.value = ""; }} />
      </div>

      {(uploading || file) && (
        <Card title={<span className="flex items-center gap-2"><FileSpreadsheet className="h-4 w-4 text-muted" />{file?.name}</span>}
          subtitle={file ? `${(file.size / 1024).toFixed(1)} KB` : undefined}>
          <div className="space-y-5">
            <div>
              <div className="mb-1 flex justify-between text-xs text-ink-2"><span>Upload</span><span className="tabular">{Math.round(progress * 100)}%</span></div>
              <div className="h-2 overflow-hidden rounded-full bg-surface-2">
                <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${progress * 100}%` }} />
              </div>
            </div>
            {error && (
              <Note tone="warn">
                {error.message}
                {error.batchId && <> — <button className="underline" onClick={() => setResult({ batch_id: error.batchId!, row_errors: [] } as unknown as UploadResult)}>view existing batch</button></>}
              </Note>
            )}
            {result && (
              <div className="grid grid-cols-3 gap-3">
                <div className="rounded-lg bg-surface-2 p-3"><div className="text-xs text-muted">Total rows</div><div className="tabular text-lg font-semibold">{fmtInt(batch?.total_rows ?? result.total_rows)}</div></div>
                <div className="rounded-lg bg-surface-2 p-3"><div className="text-xs text-muted">Accepted</div><div className="tabular text-lg font-semibold">{fmtInt(batch?.accepted_rows ?? result.accepted_rows)}</div></div>
                <div className="rounded-lg bg-surface-2 p-3"><div className="text-xs text-muted">Rejected</div><div className="tabular text-lg font-semibold">{fmtInt(batch?.rejected_rows ?? result.rejected_rows)}</div></div>
              </div>
            )}
            {result?.timestamps_inferred ? <Note>{result.timestamps_inferred} row(s) had no timestamp; the upload time was used.</Note> : null}
            {batch && <BatchProgress batch={batch} />}
            {batch?.status === "PROCESSED" && (
              <Link to="/" className="inline-flex rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white">Open dashboard →</Link>
            )}
            {(batch?.row_errors?.length ?? 0) > 0 && (
              <div>
                <h3 className="mb-2 text-sm font-semibold">Row-level validation errors</h3>
                <div className="max-h-72 overflow-auto rounded-lg border border-line">
                  <table className="w-full text-left text-xs">
                    <thead className="sticky top-0 bg-surface-2 text-muted">
                      <tr><th className="px-3 py-2 font-medium">Row</th><th className="px-3 py-2 font-medium">Field</th><th className="px-3 py-2 font-medium">Problem</th></tr>
                    </thead>
                    <tbody>
                      {batch!.row_errors!.map((e, i) => (
                        <tr key={i} className="border-t border-line">
                          <td className="tabular px-3 py-1.5">{e.row_number}</td>
                          <td className="px-3 py-1.5 font-mono">{e.field ?? "–"}</td>
                          <td className="px-3 py-1.5 text-ink-2">{e.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </Card>
      )}

      <Card title="Recent batches">
        {!history.data?.length ? <p className="text-sm text-muted">No uploads yet.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="py-2 pr-3 font-medium">File</th><th className="py-2 pr-3 font-medium">Uploaded</th>
                  <th className="py-2 pr-3 text-right font-medium">Accepted</th><th className="py-2 pr-3 text-right font-medium">Rejected</th>
                  <th className="py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {history.data.map((b) => (
                  <tr key={b.batch_id} className="border-b border-line last:border-0">
                    <td className="py-2 pr-3">{b.filename}</td>
                    <td className="py-2 pr-3 text-ink-2">{fmtDate(b.created_at, true)}</td>
                    <td className="tabular py-2 pr-3 text-right">{fmtInt(b.accepted_rows)}</td>
                    <td className="tabular py-2 pr-3 text-right">{fmtInt(b.rejected_rows)}</td>
                    <td className="py-2 text-xs">{humanize(b.status)}{b.status === "FAILED" && b.error_message ? `: ${b.error_message}` : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
