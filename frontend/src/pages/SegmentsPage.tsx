import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorState, Loading, Note } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useFilters } from "../hooks/useFilters";
import { fmtInt, humanize, truncate } from "../lib/format";
import { api } from "../services/api";
import { UI_DIMENSIONS } from "../lib/dimensions";

const DIMENSIONS: string[] = UI_DIMENSIONS.map((d) => d.key);

export function SegmentsPage() {
  const { filters, key } = useFilters();
  const [dim, setDim] = useState("country");
  const seg = useApi(() => api.segments(filters, dim).then((r) => r.data), [key, dim]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Customer segments</h1>
          <p className="text-sm text-muted">Demographics come only from supplied customer records; they are never inferred from complaint text.</p>
        </div>
        <div className="flex rounded-md border border-line bg-surface p-0.5">
          {DIMENSIONS.map((d) => (
            <button key={d} onClick={() => setDim(d)}
              className={`rounded px-2.5 py-1 text-xs font-medium ${dim === d ? "bg-accent-wash text-accent-ink" : "text-ink-2 hover:text-ink"}`}>
              {humanize(d)}
            </button>
          ))}
        </div>
      </div>
      {seg.error && <ErrorState error={seg.error} onRetry={seg.reload} />}
      {/* The previous dimension's data stays in `seg.data` while the new one loads: wait for this one */}
      {!seg.data?.segments[dim] ? (!seg.error && <Loading />) : (() => {
        const b = seg.data.segments[dim];
        const m = seg.data.issue_matrix;
        const maxCust = Math.max(...b.segments.map((s) => s.unique_customers), 1);
        const isRate = b.metric_type === "rate";
        const maxCell = Math.max(...m.cells.map((c) => c.unique_customers ?? 0), 1);
        return (
          <>
            <Note tone={isRate ? "info" : "warn"}>
              {isRate
                ? "Rates use the population denominators uploaded via POST /api/v1/analytics/populations."
                : b.rate_note}
              {b.suppressed_segments > 0 && ` ${b.suppressed_segments} segment(s) with fewer than ${b.min_customers} customers are hidden for privacy.`}
            </Note>
            <Card title={`Affected customers by ${humanize(dim).toLowerCase()}`}>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs text-muted">
                    <tr className="border-b border-line">
                      <th className="py-2 pr-3 font-medium">Segment</th>
                      <th className="py-2 pr-3 font-medium">Unique customers</th>
                      <th className="py-2 pr-3 text-right font-medium">Complaints</th>
                      <th className="py-2 pr-3 text-right font-medium">Incidents</th>
                      <th className="py-2 pr-3 text-right font-medium">Complaints / customer</th>
                      {isRate && <th className="py-2 pr-3 text-right font-medium">Affected per 1,000</th>}
                      <th className="py-2 font-medium">Top issues</th>
                    </tr>
                  </thead>
                  <tbody>
                    {b.segments.map((s) => (
                      <tr key={s.segment} className="border-b border-line align-top last:border-0">
                        <td className="py-2 pr-3 font-medium">{s.segment}</td>
                        <td className="py-2 pr-3">
                          <div className="flex items-center gap-2">
                            <div className="h-2 w-28 overflow-hidden rounded-full bg-surface-2">
                              <div className="h-full rounded-full" style={{ width: `${(100 * s.unique_customers) / maxCust}%`, background: "var(--s1)" }} />
                            </div>
                            <span className="tabular">{fmtInt(s.unique_customers)}</span>
                          </div>
                        </td>
                        <td className="tabular py-2 pr-3 text-right">{fmtInt(s.complaints)}</td>
                        <td className="tabular py-2 pr-3 text-right">{fmtInt(s.incidents)}</td>
                        <td className="tabular py-2 pr-3 text-right">{s.complaints_per_customer?.toFixed(2) ?? "–"}</td>
                        {isRate && <td className="tabular py-2 pr-3 text-right">{s.affected_rate_per_1000?.toFixed(2) ?? "n/a"}</td>}
                        <td className="py-2 text-xs text-ink-2">
                          {s.top_issues.slice(0, 3).map((t) => (
                            <div key={t.issue_id}><Link to={`/issues/${t.issue_id}`} className="hover:text-accent">{truncate(t.title, 40)}</Link> <span className="text-muted">({t.unique_customers})</span></div>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
            <Card title="Issue distribution by segment" subtitle="Unique customers per issue and segment · blank = suppressed (< privacy threshold)">
              <div className="overflow-x-auto">
                <table className="text-xs">
                  <thead>
                    <tr>
                      <th className="sticky left-0 bg-surface py-1 pr-3 text-left font-medium text-muted">Issue</th>
                      {m.segments.map((s) => <th key={s} className="px-1 py-1 text-center font-medium text-muted">{truncate(s, 14)}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {m.issues.map((iss) => (
                      <tr key={iss.issue_id}>
                        <td className="sticky left-0 max-w-[240px] truncate bg-surface py-0.5 pr-3 text-ink-2">
                          <Link to={`/issues/${iss.issue_id}`} className="hover:text-accent">{truncate(iss.title, 36)}</Link>
                        </td>
                        {m.segments.map((s) => {
                          const cell = m.cells.find((c) => c.issue_id === iss.issue_id && c.segment === s);
                          const v = cell?.unique_customers ?? null;
                          // sequential single-hue encoding: opacity of blue scales with magnitude
                          return (
                            <td key={s} className="p-0.5">
                              <div className="tabular flex h-7 min-w-[56px] items-center justify-center rounded"
                                title={`${iss.title} · ${s}: ${v ?? "suppressed"}`}
                                style={{ background: v ? `color-mix(in oklab, var(--s1) ${15 + 70 * (v / maxCell)}%, var(--surface))` : "var(--surface-2)",
                                         color: v && v / maxCell > 0.55 ? "#fff" : "var(--ink-2)" }}>
                                {v ?? ""}
                              </div>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </>
        );
      })()}
    </div>
  );
}
