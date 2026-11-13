import {
  AtSign, CheckCircle2, FileSpreadsheet, Loader2, Mail, MessageSquare, Phone, UploadCloud, Users, XCircle,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Card, Note } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { fmtDate, fmtInt, humanize } from "../lib/format";
import { ApiError, api, uploadChannel, uploadCsv, uploadDirectory } from "../services/api";
import type { Batch, ChannelInfo, DirectoryResult, RowError, UploadResult } from "../types/api";

const STAGES = ["preprocessing", "embedding", "classification", "clustering", "labeling", "deduplication", "scoring"];

type TabId = "csv" | "email" | "chat" | "social" | "voice" | "directory";

const TABS: { id: TabId; label: string; icon: typeof Mail; accept: string; multiple: boolean; hint: React.ReactNode }[] = [
  {
    id: "csv", label: "CSV", icon: FileSpreadsheet, accept: ".csv,text/csv", multiple: false,
    hint: <>One CSV with a required <Code>complaint_text</Code> column. Optional: customer_id, source, timestamp, country,
      state, age_group, student_status, income_bracket.</>,
  },
  {
    id: "email", label: "Email", icon: Mail, accept: ".eml,.mbox,message/rfc822", multiple: true,
    hint: <>One or more <Code>.eml</Code> messages or <Code>.mbox</Code> mailbox exports. Subject + body become the complaint,
      the <Code>Date</Code> header the timestamp. The sender address is matched to a customer through the customer directory.
      Auto-replies and bounces are rejected.</>,
  },
  {
    id: "chat", label: "Chat", icon: MessageSquare, accept: ".json,.jsonl,.ndjson", multiple: true,
    hint: <>Chatbot / live-chat transcripts as JSON or JSONL: one conversation per record with a <Code>messages</Code> list
      (<Code>role</Code> + <Code>text</Code>). Only the customer's messages are analysed; bot and agent turns are dropped.
      Optional customer_id, customer_email, customer_phone and demographic fields.</>,
  },
  {
    id: "social", label: "Social media", icon: AtSign, accept: ".json,.jsonl,.ndjson,.csv", multiple: true,
    hint: <>Exported posts, mentions or reviews (JSON, JSONL or CSV) with <Code>text</Code>, <Code>handle</Code> and
      <Code>created_at</Code>. Handles are matched through the customer directory; retweets / reposts are rejected.</>,
  },
  {
    id: "voice", label: "Voice calls", icon: Phone, accept: ".wav,.mp3,.m4a,.ogg,.flac,.webm,.mp4,.aac,.opus,.csv", multiple: true,
    hint: <>Call recordings, transcribed to text with Whisper. Add an optional manifest CSV
      (<Code>filename, customer_id, phone, timestamp</Code>, demographics) to set the caller and call time.</>,
  },
  {
    id: "directory", label: "Customer directory", icon: Users, accept: ".csv,text/csv", multiple: false,
    hint: <>CSV mapping <Code>customer_id</Code> to <Code>email</Code>, <Code>phone</Code> and/or <Code>social_handle</Code>
      (several values separated by <Code>;</Code>), plus optional demographics. This is how the same customer is
      recognised across channels, so repeated reports are not double-counted.</>,
  },
];

function Code({ children }: { children: React.ReactNode }) {
  return <code className="rounded bg-surface-2 px-1">{children}</code>;
}

const LINK_LABEL: Record<string, string> = {
  explicit: "customer ID in data",
  directory: "matched via directory",
  pseudonymous: "unlinked sender (pseudonymous ID)",
  anonymous: "anonymous",
};

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

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg bg-surface-2 p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="tabular text-lg font-semibold">{typeof value === "number" ? fmtInt(value) : value}</div>
    </div>
  );
}

function ErrorTable({ errors, rowLabel }: { errors: RowError[]; rowLabel: string }) {
  if (!errors.length) return null;
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold">Validation errors</h3>
      <div className="max-h-72 overflow-auto rounded-lg border border-line">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-surface-2 text-muted">
            <tr><th className="px-3 py-2 font-medium">{rowLabel}</th><th className="px-3 py-2 font-medium">Field</th><th className="px-3 py-2 font-medium">Problem</th></tr>
          </thead>
          <tbody>
            {errors.map((e, i) => (
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
  );
}

function ItemsTable({ result }: { result: UploadResult }) {
  const items = result.items ?? [];
  if (!items.length) return null;
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold">Items received</h3>
      <div className="max-h-80 overflow-auto rounded-lg border border-line">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-surface-2 text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">#</th><th className="px-3 py-2 font-medium">Item</th>
              <th className="px-3 py-2 font-medium">Status</th><th className="px-3 py-2 font-medium">Customer identity</th>
              <th className="px-3 py-2 font-medium">Detail</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => (
              <tr key={it.item} className="border-t border-line">
                <td className="tabular px-3 py-1.5">{it.item}</td>
                <td className="px-3 py-1.5 font-mono break-all">{it.label}</td>
                <td className="px-3 py-1.5">
                  {it.accepted ? <span className="inline-flex items-center gap-1"><CheckCircle2 className="h-3.5 w-3.5 text-[var(--good)]" />accepted</span>
                    : <span className="inline-flex items-center gap-1"><XCircle className="h-3.5 w-3.5 text-[var(--sev-critical)]" />rejected</span>}
                </td>
                <td className="px-3 py-1.5 text-ink-2">{it.accepted ? LINK_LABEL[it.customer_link] ?? it.customer_link : "–"}</td>
                <td className="px-3 py-1.5 text-ink-2">{it.detail ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function VoiceStatus({ info }: { info?: ChannelInfo }) {
  const t = info?.transcriber;
  if (!t) return null;
  if (!t.available) return <Note tone="warn">Voice transcription is unavailable: {t.reason}</Note>;
  return (
    <Note>
      Transcription: {t.backend === "local" ? `local Whisper “${t.model}” on ${t.device}` : `OpenAI (${t.backend})`}.
      {t.backend === "local" && !t.loaded && " The first voice upload downloads the model (~150 MB) and takes longer."}
      {" "}Recordings are transcribed during upload, so large batches take a while.
    </Note>
  );
}

export function UploadPage() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.id === params.get("channel"))?.id ?? "csv") as TabId;
  const spec = TABS.find((t) => t.id === tab)!;

  const [files, setFiles] = useState<File[]>([]);
  const [drag, setDrag] = useState(false);
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<UploadResult>();
  const [directory, setDirectory] = useState<DirectoryResult>();
  const [batch, setBatch] = useState<Batch>();
  const [error, setError] = useState<{ message: string; batchId?: string }>();
  const inputRef = useRef<HTMLInputElement>(null);
  const history = useApi(() => api.batches().then((r) => r.data), [batch?.status, directory]);
  const channels = useApi(() => api.channels().then((r) => r.data), [tab === "voice" ? result : null]);
  const [retrying, setRetrying] = useState<string>();

  // Keep the history table live while any batch (e.g. a retried one) is still running
  const historyActive = history.data?.some((b) => b.status === "PROCESSING" || b.status === "UPLOADED");
  useEffect(() => {
    if (!historyActive) return;
    const t = setTimeout(history.reload, 1500);
    return () => clearTimeout(t);
  }, [history.data, historyActive, history.reload]);

  const retry = async (batchId: string) => {
    setRetrying(batchId);
    try {
      await api.process(batchId);
    } catch (e) {
      setError({ message: (e as Error).message });
    } finally {
      setRetrying(undefined);
      history.reload();
    }
  };

  const reset = () => {
    setFiles([]); setResult(undefined); setDirectory(undefined); setBatch(undefined); setError(undefined); setProgress(0);
  };
  const selectTab = (id: TabId) => {
    if (uploading) return;
    reset();
    setParams((p) => { const n = new URLSearchParams(p); n.set("channel", id); return n; }, { replace: true });
  };

  // Poll batch status until it finishes
  useEffect(() => {
    if (!result) return;
    let stop = false;
    const poll = async () => {
      try {
        const b = (await api.batch(result.batch_id)).data;
        if (stop) return;
        setBatch(b);
        // Keep polling only while a pipeline run can actually finish it
        const running = b.status === "PROCESSING" || (b.status === "UPLOADED" && result.processing_started !== false);
        if (running) setTimeout(poll, 800);
      } catch (e) {
        if (!stop) setError({ message: (e as Error).message });
      }
    };
    poll();
    return () => { stop = true; };
  }, [result]);

  const start = useCallback(async (picked: File[]) => {
    if (!picked.length) return;
    const chosen = spec.multiple ? picked : picked.slice(0, 1);
    reset();
    setFiles(chosen);
    setUploading(true);
    try {
      if (tab === "directory") setDirectory((await uploadDirectory(chosen[0], setProgress)).data);
      else if (tab === "csv") setResult((await uploadCsv(chosen[0], setProgress)).data);
      else setResult((await uploadChannel(tab, chosen, setProgress)).data);
    } catch (e) {
      const err = e as ApiError;
      const details = err.details as { batch_id?: string } | undefined;
      setError({ message: err.message, batchId: details?.batch_id });
    } finally {
      setUploading(false);
    }
  }, [spec.multiple, tab]);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDrag(false);
    start(Array.from(e.dataTransfer.files ?? []));
  };

  const voiceInfo = channels.data?.find((c) => c.channel === "voice");
  const voiceBlocked = tab === "voice" && voiceInfo?.transcriber && !voiceInfo.transcriber.available;
  const totalSize = files.reduce((n, f) => n + f.size, 0);
  const Icon = spec.icon;
  const links = Object.entries(result?.customer_links ?? {});

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Upload complaints</h1>
        <p className="text-sm text-muted">
          Bring in complaints from every channel. Each upload becomes a batch that runs through the same pipeline, and repeated reports of
          the same problem by the same customer are merged across channels.
        </p>
      </div>

      <div role="tablist" aria-label="Channel" className="flex flex-wrap gap-1 rounded-lg border border-line bg-surface p-1">
        {TABS.map((t) => {
          const TIcon = t.icon;
          const active = t.id === tab;
          return (
            <button key={t.id} role="tab" aria-selected={active} disabled={uploading} onClick={() => selectTab(t.id)}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors ${
                active ? "bg-accent text-white" : "text-ink-2 hover:bg-surface-2"} disabled:opacity-60`}>
              <TIcon className="h-4 w-4" />{t.label}
            </button>
          );
        })}
      </div>

      <p className="text-sm text-ink-2">{spec.hint}</p>
      {tab === "voice" && <VoiceStatus info={voiceInfo} />}
      {tab === "directory" && (
        <Note>Contact details are normalised and stored only as salted hashes; the raw emails, phone numbers and handles are discarded.
          Complaints already received from an unlinked sender are moved to the matching customer and the pipeline re-runs.</Note>
      )}

      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        onClick={() => !uploading && !voiceBlocked && inputRef.current?.click()}
        role="button"
        tabIndex={0}
        aria-disabled={uploading || !!voiceBlocked}
        onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
        className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors ${
          drag ? "border-accent bg-accent-wash" : "border-line bg-surface hover:bg-surface-2"} ${voiceBlocked ? "pointer-events-none opacity-50" : ""}`}
      >
        <UploadCloud className="h-8 w-8 text-accent" />
        <div className="text-sm font-medium">
          {spec.multiple ? `Drop ${spec.label.toLowerCase()} files here, or click to browse` : "Drop a CSV file here, or click to browse"}
        </div>
        <div className="text-xs text-muted">
          {spec.accept.split(",").filter((a) => a.startsWith(".")).join(" ")}
          {tab !== "directory" && " · identical uploads are detected by SHA-256 and rejected"}
        </div>
        <input ref={inputRef} type="file" accept={spec.accept} multiple={spec.multiple} className="hidden"
          onChange={(e) => { start(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
      </div>

      {(uploading || files.length > 0) && (
        <Card title={<span className="flex items-center gap-2"><Icon className="h-4 w-4 text-muted" />
          {files.length === 1 ? files[0].name : `${files.length} files`}</span>}
          subtitle={`${(totalSize / 1024).toFixed(1)} KB`}>
          <div className="space-y-5">
            <div>
              <div className="mb-1 flex justify-between text-xs text-ink-2">
                <span>{uploading && progress >= 1 ? (tab === "voice" ? "Transcribing recordings…" : "Validating…") : "Upload"}</span>
                <span className="tabular">{Math.round(progress * 100)}%</span>
              </div>
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

            {directory && (
              <>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Stat label="Customers" value={directory.customers} />
                  <Stat label="Contacts stored" value={directory.contacts_stored} />
                  <Stat label="Complaints relinked" value={directory.complaints_relinked} />
                  <Stat label="Customers in directory" value={directory.stats.customers_with_contacts} />
                </div>
                {directory.recluster_started && <Note>Existing complaints were relinked; deduplication and scoring are re-running in the background.</Note>}
                <ErrorTable errors={directory.row_errors} rowLabel="Row" />
              </>
            )}

            {result && (
              <div className="grid grid-cols-3 gap-3">
                <Stat label={tab === "csv" ? "Total rows" : "Items"} value={batch?.total_rows ?? result.total_rows} />
                <Stat label="Accepted" value={batch?.accepted_rows ?? result.accepted_rows} />
                <Stat label="Rejected" value={batch?.rejected_rows ?? result.rejected_rows} />
              </div>
            )}
            {links.length > 0 && (
              <div className="flex flex-wrap gap-2 text-xs">
                <span className="text-muted">Customer identity:</span>
                {links.map(([k, n]) => (
                  <span key={k} className="rounded-full bg-surface-2 px-2 py-0.5"><span className="tabular font-medium">{n}</span> {LINK_LABEL[k] ?? k}</span>
                ))}
              </div>
            )}
            {result?.timestamps_inferred ? <Note>{result.timestamps_inferred} item(s) had no timestamp; the upload time was used.</Note> : null}
            {batch && <BatchProgress batch={batch} />}
            {batch?.status === "PROCESSED" && (
              <Link to="/" className="inline-flex rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white">Open dashboard →</Link>
            )}
            {result && <ItemsTable result={result} />}
            {!result?.items?.length && <ErrorTable errors={batch?.row_errors ?? []} rowLabel="Row" />}
          </div>
        </Card>
      )}

      <Card title="Recent batches">
        {!history.data?.length ? <p className="text-sm text-muted">No uploads yet.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="py-2 pr-3 font-medium">File</th><th className="py-2 pr-3 font-medium">Channel</th>
                  <th className="py-2 pr-3 font-medium">Uploaded</th>
                  <th className="py-2 pr-3 text-right font-medium">Accepted</th><th className="py-2 pr-3 text-right font-medium">Rejected</th>
                  <th className="py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {history.data.map((b) => (
                  <tr key={b.batch_id} className="border-b border-line last:border-0">
                    <td className="py-2 pr-3">{b.filename}</td>
                    <td className="py-2 pr-3 text-ink-2">{humanize(b.channel ?? "CSV")}</td>
                    <td className="py-2 pr-3 text-ink-2">{fmtDate(b.created_at, true)}</td>
                    <td className="tabular py-2 pr-3 text-right">{fmtInt(b.accepted_rows)}</td>
                    <td className="tabular py-2 pr-3 text-right">{fmtInt(b.rejected_rows)}</td>
                    <td className="py-2 text-xs">
                      {humanize(b.status)}
                      {b.status === "PROCESSING" && b.stage ? `: ${humanize(b.stage)}` : ""}
                      {b.status === "FAILED" && b.accepted_rows === 0 && b.error_message ? `: ${b.error_message}` : ""}
                      {b.status === "FAILED" && b.accepted_rows > 0 && (
                        <>
                          {b.error_message ? `: ${b.error_message.split(". Re-run")[0]}` : ""}{" "}
                          <button onClick={() => retry(b.batch_id)} disabled={!!retrying || historyActive}
                            className="ml-1 rounded border border-line px-2 py-0.5 font-medium text-accent-ink hover:bg-accent-wash disabled:opacity-50">
                            {retrying === b.batch_id ? "Starting…" : "Retry"}
                          </button>
                        </>
                      )}
                    </td>
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
