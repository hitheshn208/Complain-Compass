import { Search, X } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Card, EmptyState, ErrorState, Loading, Pagination, SeverityBadge } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useFilters } from "../hooks/useFilters";
import { fmtDate, fmtInt, humanize, sourceColor } from "../lib/format";
import { api } from "../services/api";
import type { Complaint } from "../types/api";

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
                        {c.link_type === "DUPLICATE" && <span className="rounded bg-surface-2 px-1.5 font-medium text-ink-2">duplicate report</span>}
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
        {selected && <SimilarPanel complaint={selected} onClose={() => setSelected(undefined)} />}
      </div>
    </div>
  );
}
