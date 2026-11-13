import { Activity, AlertOctagon, Boxes, Copy, MessageSquareText, TrendingUp, Users } from "lucide-react";
import { Link } from "react-router-dom";
import { CountBars, PriorityBars, SeverityBreakdown, VolumeChart } from "../components/charts";
import { Card, EmptyState, ErrorState, Loading, Note, PriorityMeter, SeverityBadge, StatTile } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useFilters } from "../hooks/useFilters";
import { fmtInt, humanize, sourceColor } from "../lib/format";
import { api } from "../services/api";

export function OverviewPage() {
  const { filters, key } = useFilters();
  const overview = useApi(() => api.overview(filters).then((r) => r.data), [key]);
  const trends = useApi(() => api.trends(filters).then((r) => r.data), [key]);
  const segments = useApi(() => api.segments(filters, "age_group").then((r) => r.data), [key]);

  if (overview.error) return <ErrorState error={overview.error} onRetry={overview.reload} />;
  if (!overview.data) return <Loading label="Loading overview" />;
  const o = overview.data;

  if (o.total_reports === 0) {
    return (
      <Card>
        <EmptyState title={o.filtered ? "No complaints match these filters" : "No complaints yet"}>
          {o.filtered ? "Try widening the date range or clearing filters." : (
            <>Upload a CSV on the <Link className="text-accent underline" to="/upload">Upload data</Link> page to start the analysis.</>
          )}
        </EmptyState>
      </Card>
    );
  }

  const emerging = trends.data?.emerging;
  const ageSeg = segments.data?.segments.age_group;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Complaint overview</h1>
          <p className="text-sm text-muted">
            {o.filtered ? "Filtered view" : "All data"} · Classification: {o.llm_enabled ? "GPT-4o-mini" : "rule-based fallback (no OpenAI key)"}
          </p>
        </div>
      </div>

      {o.grouping_mode === "category" && (
        <Note>
          Small dataset ({fmtInt(o.processed_complaints)} complaints): issues are grouped by complaint category.
          Semantic clustering, which also separates different problems within a category, starts at{" "}
          {fmtInt(o.semantic_min_complaints)} complaints.
        </Note>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        <StatTile label="Complaint reports" value={fmtInt(o.total_reports)} icon={<MessageSquareText className="h-4 w-4" />}
          hint={`${fmtInt(o.total_incidents)} distinct incidents`} />
        <StatTile label="Unique affected customers" value={fmtInt(o.unique_affected_customers)} icon={<Users className="h-4 w-4" />}
          hint={`${fmtInt(o.verified_customers)} verified · ${fmtInt(o.anonymous_incidents)} anonymous`} />
        <StatTile label="Duplicate reports" value={fmtInt(o.duplicate_reports)} icon={<Copy className="h-4 w-4" />}
          hint="Same customer, same incident" />
        <StatTile label="Issue clusters" value={fmtInt(o.issue_clusters)} icon={<Boxes className="h-4 w-4" />}
          hint={`${fmtInt(o.noise_complaints)} unclustered complaints`} />
        <StatTile label="Critical / high issues" value={fmtInt(o.critical_high_issues)} icon={<AlertOctagon className="h-4 w-4" />}
          tone={o.critical_high_issues ? "alert" : "default"} hint="By issue severity score" />
        <StatTile label="Emerging issues" value={o.emerging_limited_data ? "–" : fmtInt(o.emerging_issues)}
          icon={<TrendingUp className="h-4 w-4" />} tone={o.emerging_issues ? "alert" : "default"}
          hint={o.emerging_limited_data ? "Insufficient history" : "z-score vs 14-day baseline"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Complaint volume over time" subtitle="Daily reports and incidents (duplicate reports collapsed)">
          {trends.data ? <VolumeChart data={trends.data.series} /> : <Loading />}
        </Card>
        <Card title="Severity distribution" subtitle="Complaint-level classification">
          <SeverityBreakdown dist={o.severity_distribution} />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Top 10 issues by priority"
          subtitle="P = 0.35·Impact + 0.25·Severity + 0.20·Recurrence + 0.10·Growth + 0.10·Business"
          action={<Link to={{ pathname: "/issues" }} className="text-xs font-medium text-accent hover:underline">All issues →</Link>}>
          {o.top_issues.length ? <PriorityBars issues={o.top_issues} /> : <EmptyState title="No scored issues yet" />}
        </Card>
        <Card title="Emerging issue alerts" subtitle={emerging ? `Method: ${emerging.method}` : undefined}>
          {!emerging ? <Loading /> : (
            <div className="space-y-3">
              {emerging.limited_data && <Note tone="warn">{emerging.note ?? "Limited history: alerts are not reliable."}</Note>}
              <ul className="divide-y divide-[var(--border)]">
                {emerging.issues.slice(0, 6).map((e) => (
                  <li key={e.issue_id} className="flex items-center justify-between gap-3 py-2">
                    <Link to={`/issues/${e.issue_id}`} className="min-w-0 truncate text-sm text-ink hover:text-accent">{e.title}</Link>
                    <span className="flex shrink-0 items-center gap-2 text-xs">
                      {e.is_emerging && (
                        <span className="flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 font-medium text-ink">
                          <Activity className="h-3 w-3 text-[var(--sev-critical)]" /> Emerging
                        </span>
                      )}
                      <span className="tabular text-ink-2">z={e.z_score.toFixed(1)}</span>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="text-[11px] text-muted">
                Recent {emerging.params.recent_days}-day daily incident rate vs prior {emerging.params.baseline_days}-day baseline;
                flagged at z ≥ {emerging.params.z_threshold} with ≥ {emerging.params.min_recent_count} recent incidents.
              </p>
            </div>
          )}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Complaints by source">
          <CountBars rows={o.by_source} colorFor={(k) => sourceColor(k)} />
        </Card>
        <Card title="Geographic distribution" subtitle="Reports by customer country (Unknown = not provided)">
          <CountBars rows={o.by_country} colorFor={() => "var(--s1)"} />
        </Card>
        <Card title="Age group comparison" subtitle={ageSeg?.metric_type === "rate" ? "Affected customers per 1,000 in segment" : "Unique affected customers (counts, not rates)"}>
          {!ageSeg ? <Loading /> : (
            <CountBars
              valueKey="unique_customers"
              rows={ageSeg.segments.map((s) => ({ key: s.segment, reports: s.complaints, unique_customers: s.unique_customers }))}
              colorFor={() => "var(--s1)"}
            />
          )}
        </Card>
      </div>

      <Card title="Top issues" subtitle="Click an issue for its evidence and score breakdown">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted">
              <tr className="border-b border-line">
                <th className="py-2 pr-3 font-medium">#</th>
                <th className="py-2 pr-3 font-medium">Issue</th>
                <th className="py-2 pr-3 font-medium">Severity</th>
                <th className="py-2 pr-3 text-right font-medium">Customers</th>
                <th className="py-2 pr-3 text-right font-medium">Reports</th>
                <th className="py-2 font-medium">Priority</th>
              </tr>
            </thead>
            <tbody>
              {o.top_issues.map((i, idx) => (
                <tr key={i.issue_id} className="border-b border-line last:border-0 hover:bg-surface-2">
                  <td className="tabular py-2 pr-3 text-muted">{idx + 1}</td>
                  <td className="py-2 pr-3">
                    <Link to={`/issues/${i.issue_id}`} className="font-medium text-ink hover:text-accent">{i.title}</Link>
                    <div className="text-xs text-muted">{humanize(i.category)}</div>
                  </td>
                  <td className="py-2 pr-3"><SeverityBadge severity={i.severity} /></td>
                  <td className="tabular py-2 pr-3 text-right">{fmtInt(i.unique_affected_customers)}</td>
                  <td className="tabular py-2 pr-3 text-right">{fmtInt(i.total_reports)}</td>
                  <td className="py-2"><PriorityMeter value={i.priority_score} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
