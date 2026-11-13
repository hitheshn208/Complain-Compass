import { Activity, ArrowDown, ArrowUp } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Card, EmptyState, ErrorState, Loading, PriorityMeter, SeverityBadge, StatusPill } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useFilters } from "../hooks/useFilters";
import { fmtInt, fmtScore, humanize } from "../lib/format";
import { api } from "../services/api";
import type { IssueSummary } from "../types/api";

type SortKey = "priority_score" | "unique_affected_customers" | "total_reports" | "severity_score" | "growth_score";

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "unique_affected_customers", label: "Customers" },
  { key: "total_reports", label: "Reports" },
  { key: "severity_score", label: "Severity score" },
  { key: "growth_score", label: "Growth" },
  { key: "priority_score", label: "Priority" },
];

export function IssuesPage() {
  const { filters, key } = useFilters();
  const { search } = useLocation();
  const issues = useApi(() => api.issues(filters, 1, 200).then((r) => r.data), [key]);
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "priority_score", desc: true });
  const [q, setQ] = useState("");

  const rows = useMemo(() => {
    const list = (issues.data ?? []).filter((i) => !q || i.title.toLowerCase().includes(q.toLowerCase()));
    return [...list].sort((a: IssueSummary, b: IssueSummary) => {
      const d = (a[sort.key] ?? 0) - (b[sort.key] ?? 0);
      return sort.desc ? -d : d;
    });
  }, [issues.data, sort, q]);

  if (issues.error) return <ErrorState error={issues.error} onRetry={issues.reload} />;
  if (!issues.data) return <Loading label="Loading issues" />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Discovered issues</h1>
          <p className="text-sm text-muted">Recurring problems: differently worded complaints about the same problem are grouped together (semantic clustering; small datasets are grouped by complaint category). Complaints that fit no group are not shown.</p>
        </div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search issues…"
          className="h-8 w-64 rounded-md border border-line bg-surface px-2.5 text-sm outline-none focus:border-accent" />
      </div>
      <Card>
        {rows.length === 0 ? (
          <EmptyState title={q ? "No issues match" : "No recurring issues yet"}>
            {!q && "An issue needs at least 2 complaints about the same kind of problem. Upload more complaints to see issues here."}
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="py-2 pr-3 font-medium">Issue</th>
                  <th className="py-2 pr-3 font-medium">Severity</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  {COLUMNS.map((c) => (
                    <th key={c.key} className="py-2 pr-3 text-right font-medium">
                      <button className="inline-flex items-center gap-1 hover:text-ink"
                        onClick={() => setSort((s) => ({ key: c.key, desc: s.key === c.key ? !s.desc : true }))}>
                        {c.label}
                        {sort.key === c.key && (sort.desc ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />)}
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((i) => (
                  <tr key={i.issue_id} className="border-b border-line last:border-0 hover:bg-surface-2">
                    <td className="max-w-md py-2.5 pr-3">
                      <Link to={{ pathname: `/issues/${i.issue_id}`, search }} className="font-medium text-ink hover:text-accent">{i.title}</Link>
                      <div className="flex items-center gap-2 text-xs text-muted">
                        {humanize(i.category)}
                        {i.label_source === "manual" && <span>· edited</span>}
                        {i.is_emerging && (
                          <span className="flex items-center gap-1 font-medium text-ink">
                            <Activity className="h-3 w-3 text-[var(--sev-critical)]" /> Emerging
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="py-2.5 pr-3"><SeverityBadge severity={i.severity} /></td>
                    <td className="py-2.5 pr-3"><StatusPill status={i.status} /></td>
                    <td className="tabular py-2.5 pr-3 text-right">{fmtInt(i.unique_affected_customers)}</td>
                    <td className="tabular py-2.5 pr-3 text-right">{fmtInt(i.total_reports)}</td>
                    <td className="tabular py-2.5 pr-3 text-right">{fmtScore(i.severity_score, 0)}</td>
                    <td className="tabular py-2.5 pr-3 text-right">{fmtScore(i.growth_score, 0)}</td>
                    <td className="py-2.5 pr-3"><div className="flex justify-end"><PriorityMeter value={i.priority_score} /></div></td>
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
