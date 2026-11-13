import { Link2, Search, X } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Card, EmptyState, ErrorState, Loading, Pagination, SeverityBadge } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useFilters } from "../hooks/useFilters";
import { fmtDate, fmtInt, humanize, sourceColor } from "../lib/format";
import { api } from "../services/api";
import type { Complaint, IncidentReport } from "../types/api";

/** Plain-language reason a report was attached to its incident. */
function matchReason(r: IncidentReport): string {
  if (r.link_type === "CANONICAL") return "First report: opened this incident";
  const e = r.evidence ?? {};
  if (e.rule === "T1_shared_reference") {
    return `Same customer, same order ${(e.shared_reference_ids ?? []).join(", ")}`;
  }
  if (e.rule === "T2_identity_time_semantic_topic") {
    const parts = ["Same customer"];
    if (e.days_since_last_report != null) parts.push(`${e.days_since_last_report.toFixed(1)} days after the previous report`);
    if (e.max_semantic_similarity != null) parts.push(`${Math.round(e.max_semantic_similarity * 100)}% similar meaning`);
    if (e.same_issue_cluster) parts.push("same issue");
    else if (e.same_category) parts.push("same category");
    return parts.join(" · ");
  }
  return humanize(e.rule ?? "linked");
}

function channelLabel(r: IncidentReport): string {
  return r.upload_channel === "CSV" ? `${humanize(r.source)} (CSV import)` : humanize(r.source);
}

function IncidentPanel({ complaint, onClose }: { complaint: Complaint; onClose: () => void }) {
  const detail = useApi(() => api.complaint(complaint.complaint_id).then((r) => r.data), [complaint.complaint_id]);
  const reports = detail.data?.incident_reports ?? [];
  const channels = new Set(reports.map((r) => humanize(r.source).toLowerCase()));
  const title = reports.length > 1
    ? `Same incident: ${reports.length} reports across ${channels.size} channel${channels.size === 1 ? "" : "s"}`
    : "Same incident";
  return (
    <Card title={title}
      subtitle={reports.length > 1 ? "One customer, one problem: counted once (1 incident, 1 affected customer)" : undefined}
      action={<button onClick={onClose} className="rounded p-1 text-muted hover:bg-surface-2"><X className="h-4 w-4" /></button>}>
      {detail.error && <ErrorState error={detail.error} onRetry={detail.reload} />}
      {!detail.data ? !detail.error && <Loading /> : reports.length <= 1 ? (
        <p className="text-sm text-muted">
          {complaint.incident_id
            ? "No other reports of this incident: the customer has not reported this problem anywhere else."
            : complaint.customer_id
              ? "Not linked to an incident yet."
              : "Anonymous complaint: without a customer identity it is never merged with other reports."}
        </p>
      ) : (
        <ol className="space-y-3">
          {reports.map((r) => {
            const self = r.complaint_id === complaint.complaint_id;
            return (
              <li key={r.complaint_id} className={`rounded-lg border p-2.5 ${self ? "border-accent bg-accent-wash" : "border-line"}`}>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                  <span className="flex items-center gap-1 font-medium text-ink">
                    <span className="h-2 w-2 rounded-sm" style={{ background: sourceColor(r.source) }} />{channelLabel(r)}
                  </span>
                  <span className="text-muted">{fmtDate(r.timestamp, true)}</span>
                  {self && <span className="rounded bg-surface px-1.5 font-medium text-accent-ink">this complaint</span>}
                </div>
                <p className="mt-1 line-clamp-3 text-sm text-ink">{r.normalized_text ?? r.original_text}</p>
                <div className="mt-1 flex items-start justify-between gap-2 text-xs text-ink-2">
                  <span className="flex items-start gap-1"><Link2 className="mt-0.5 h-3 w-3 shrink-0" />{matchReason(r)}</span>
                  {r.link_type === "DUPLICATE" && (
                    <span className="tabular shrink-0 rounded bg-surface-2 px-1.5 py-0.5">{Math.round(r.match_confidence * 100)}%</span>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}

function SimilarPanel({ complaint, onClose }: { complaint: Complaint; onClose: () => void }) {
  const sim = useApi(() => api.similar(complaint.complaint_id, 5).then((r) => r.data), [complaint.complaint_id]);
  return (
    <Card title="Semantically similar complaints" subtitle="Similarity does not imply the same customer or incident"
      action={<button onClick={onClose} className="rounded p-1 text-muted hover:bg-surface-2"><X className="h-4 w-4" /></button>}>
      <p className="mb-3 rounded-lg bg-surface-2 p-3 text-sm">“{complaint.normalized_text ?? complaint.original_text}”</p>
      {sim.error && <ErrorState error={sim.error} />}
      {!sim.data ? !sim.error && <Loading /> : (
        <ul className="divide-y divide-[var(--border)]">
          {sim.data.map((s) => (
            <li key={s.complaint_id} className="py-2">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm text-ink">{s.text}</p>
                <span className="tabular shrink-0 rounded bg-surface-2 px-1.5 py-0.5 text-xs">{(s.similarity * 100).toFixed(0)}%</span>
              </div>
              <div className="mt-0.5 text-xs text-muted">
                {humanize(s.source)} · {fmtDate(s.timestamp)} · {s.customer_id === complaint.customer_id && s.customer_id ? "same customer" : "different/unknown customer"}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function ComplaintsPage() {
  const { filters, key } = useFilters();
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") ?? 1);
  const issueId = params.get("issue_id") ?? undefined;
  const search = params.get("q") ?? undefined;
  const [draft, setDraft] = useState(search ?? "");
  const [selected, setSelected] = useState<Complaint>();
  const res = useApi(() => api.complaints(filters, { page, pageSize: 25, issueId, search }), [key, page, issueId, search]);

  const setParam = (name: string, value?: string) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(name, value);
      else next.delete(name);
      if (name !== "page") next.delete("page");
      return next;
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Complaints</h1>
          <p className="text-sm text-muted">Original text is preserved; PII is masked in the analysed text.</p>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); setParam("q", draft.trim() || undefined); }} className="flex items-center gap-2">
          <div className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-surface px-2">
            <Search className="h-3.5 w-3.5 text-muted" />
            <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Search text…" className="w-56 bg-transparent text-sm outline-none" />
          </div>
        </form>
      </div>
      {issueId && (
        <div className="flex items-center gap-2 text-xs text-ink-2">
          Showing complaints for one issue
          <button onClick={() => setParam("issue_id")} className="flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 hover:text-ink"><X className="h-3 w-3" />clear</button>
        </div>
      )}
      <div className={`grid gap-4 ${selected ? "lg:grid-cols-[1fr_400px]" : ""}`}>
        <Card>
          {res.error && <ErrorState error={res.error} onRetry={res.reload} />}
          {!res.data ? !res.error && <Loading /> : res.data.data.length === 0 ? <EmptyState title="No complaints match" /> : (
            <>
              <div className="mb-2 text-xs text-muted">{fmtInt(res.data.meta.total)} complaints</div>
              <ul className="divide-y divide-[var(--border)]">
                {res.data.data.map((c) => (
                  <li key={c.complaint_id}>
                    <button onClick={() => setSelected(c)}
                      className={`w-full rounded-md px-2 py-2.5 text-left hover:bg-surface-2 ${selected?.complaint_id === c.complaint_id ? "bg-accent-wash" : ""}`}>
                      <p className="line-clamp-2 text-sm text-ink">{c.original_text}</p>
                      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                        <SeverityBadge severity={c.severity} />
                        <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: sourceColor(c.source) }} />{humanize(c.source)}</span>
                        <span>{fmtDate(c.timestamp, true)}</span>
                        <span>{c.customer_id ?? "anonymous"}</span>
                        {(c.incident_report_count ?? 0) > 1 && (
                          <span className="flex items-center gap-1 rounded bg-surface-2 px-1.5 font-medium text-ink-2">
                            <Link2 className="h-3 w-3" />{c.incident_report_count} reports · 1 incident
                          </span>
                        )}
                        {c.issue_id ? (
                          <Link onClick={(e) => e.stopPropagation()} to={`/issues/${c.issue_id}`} className="text-accent hover:underline">{c.issue_title}</Link>
                        ) : <span>unclustered</span>}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
              <Pagination page={page} pages={Number(res.data.meta.pages ?? 1)} onPage={(p) => setParam("page", String(p))} />
            </>
          )}
        </Card>
        {selected && (
          <div className="space-y-4">
            <IncidentPanel complaint={selected} onClose={() => setSelected(undefined)} />
            <SimilarPanel complaint={selected} onClose={() => setSelected(undefined)} />
          </div>
        )}
      </div>
    </div>
  );
}
