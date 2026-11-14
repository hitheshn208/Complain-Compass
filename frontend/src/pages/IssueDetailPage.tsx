import { Activity, ArrowLeft, Check, FlaskConical, Pencil, X } from "lucide-react";
import { useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { CountBars, SeverityBreakdown, VolumeChart } from "../components/charts";
import { Card, ErrorState, Loading, Note, PriorityMeter, SeverityBadge, StatTile, StatusPill } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useFilters } from "../hooks/useFilters";
import { fmtDate, fmtInt, fmtScore, humanize, sourceColor } from "../lib/format";
import { api } from "../services/api";
import { UI_DIMENSIONS } from "../lib/dimensions";
import type { IssueDetail, SegmentBreakdown } from "../types/api";

const COMPONENT_ORDER = ["impact", "severity", "recurrence", "growth", "business"];

function ScoreBreakdown({ issue }: { issue: IssueDetail }) {
  const b = issue.breakdown;
  if (!b) return <p className="text-sm text-muted">Not scored yet.</p>;
  return (
    <div className="space-y-3">
      {COMPONENT_ORDER.map((k) => {
        const c = b.components[k];
        if (!c) return null;
        return (
          <div key={k}>
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="text-ink-2">{c.label} <span className="text-muted">× {c.weight.toFixed(2)}</span></span>
              <span className="tabular text-ink">
                {c.score.toFixed(1)} <span className="text-muted">→ +{c.contribution.toFixed(1)}</span>
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
              <div className="h-full rounded-full bg-accent" style={{ width: `${c.score}%` }} />
            </div>
          </div>
        );
      })}
      <div className="flex items-center justify-between border-t border-line pt-2 text-sm">
        <span className="font-medium">Priority score</span>
        <span className="tabular font-semibold">{fmtScore(issue.priority_score)}</span>
      </div>
      {issue.explanation && <p className="text-xs leading-relaxed text-ink-2">{issue.explanation}</p>}
      <details className="text-xs text-muted">
        <summary className="cursor-pointer select-none hover:text-ink">Raw scoring inputs</summary>
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
          {Object.entries(b.inputs).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="truncate">{humanize(k)}</dt>
              <dd className="tabular text-right text-ink-2">{String(v)}</dd>
            </div>
          ))}
        </dl>
      </details>
    </div>
  );
}

function DemographicTable({ seg }: { seg: SegmentBreakdown }) {
  if (!seg.segments.length) return <p className="text-xs text-muted">No segments meet the privacy threshold.</p>;
  const max = Math.max(...seg.segments.map((s) => s.unique_customers), 1);
  return (
    <ul className="space-y-1.5">
      {seg.segments.map((s) => (
        <li key={s.segment} className="grid grid-cols-[110px_1fr_48px] items-center gap-2 text-xs">
          <span className="truncate text-ink-2">{s.segment}</span>
          <div className="h-2 overflow-hidden rounded-full bg-surface-2">
            <div className="h-full rounded-full" style={{ width: `${(100 * s.unique_customers) / max}%`, background: "var(--s1)" }} />
          </div>
          <span className="tabular text-right text-ink">{fmtInt(s.unique_customers)}</span>
        </li>
      ))}
    </ul>
  );
}

function EditIssue({ issue, onSaved }: { issue: IssueDetail; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(issue.title);
  const [status, setStatus] = useState(issue.status);
  const [err, setErr] = useState<string>();
  const save = async () => {
    try {
      await api.patchIssue(issue.issue_id, { title, status });
      setEditing(false);
      onSaved();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  if (!editing) {
    return (
      <button onClick={() => setEditing(true)} className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs font-medium text-ink-2 hover:bg-surface-2">
        <Pencil className="h-3.5 w-3.5" /> Edit
      </button>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input value={title} onChange={(e) => setTitle(e.target.value)} className="h-8 w-72 rounded-md border border-line bg-surface px-2 text-sm outline-none focus:border-accent" />
      <select value={status} onChange={(e) => setStatus(e.target.value)} className="h-8 rounded-md border border-line bg-surface px-2 text-sm">
        {["OPEN", "INVESTIGATING", "RESOLVED"].map((s) => <option key={s} value={s}>{humanize(s)}</option>)}
      </select>
      <button onClick={save} className="flex h-8 items-center gap-1 rounded-md bg-accent px-2.5 text-xs font-medium text-white"><Check className="h-3.5 w-3.5" />Save</button>
      <button onClick={() => setEditing(false)} className="flex h-8 items-center rounded-md px-2 text-xs text-ink-2 hover:bg-surface-2"><X className="h-3.5 w-3.5" /></button>
      {err && <span className="text-xs text-[var(--sev-critical)]">{err}</span>}
    </div>
  );
}

export function IssueDetailPage() {
  const { id = "" } = useParams();
  const { search } = useLocation();
  const { filters, key } = useFilters();
  const issue = useApi(() => api.issue(id, filters).then((r) => r.data), [id, key]);

  if (issue.error) return <ErrorState error={issue.error} onRetry={issue.reload} />;
  if (!issue.data) return <Loading label="Loading issue" />;
  const i = issue.data;
  const growthLimited = Boolean(i.breakdown?.inputs.growth_insufficient_history);

  return (
    <div className="space-y-6">
      <div>
        <Link to={{ pathname: "/issues", search }} className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-ink">
          <ArrowLeft className="h-3.5 w-3.5" /> All issues
        </Link>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <SeverityBadge severity={i.severity} />
              <StatusPill status={i.status} />
              <span className="text-xs text-muted">{humanize(i.category)} · {i.cluster_id}</span>
              {i.emerging?.is_emerging && (
                <span className="flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-xs font-medium">
                  <Activity className="h-3 w-3 text-[var(--sev-critical)]" /> Emerging (z={i.emerging.z_score.toFixed(1)})
                </span>
              )}
            </div>
            <h1 className="text-xl font-semibold tracking-tight">{i.title}</h1>
            {i.description && <p className="mt-1 max-w-3xl text-sm text-ink-2">{i.description}</p>}
            <p className="mt-1 text-xs text-muted">
              Label source: {i.label_source === "llm" ? "GPT-4o-mini" : i.label_source === "manual" ? "edited by analyst" : "automatic (keywords, no LLM)"}
            </p>
          </div>
          <EditIssue issue={i} onSaved={issue.reload} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatTile label="Priority score" value={<PriorityMeter value={i.priority_score} />} />
        <StatTile label="Unique affected customers" value={fmtInt(i.unique_affected_customers)} />
        <StatTile label="Total reports" value={fmtInt(i.total_reports)} hint={`${fmtInt(i.total_incidents)} incidents`} />
        <StatTile label="Severity score" value={fmtScore(i.severity_score, 0)} hint="0-100" />
        <StatTile label="Growth score" value={growthLimited ? "50" : fmtScore(i.growth_score, 0)}
          hint={growthLimited ? "Neutral: <14 days of history" : "50 = flat"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Growth trend" subtitle="Daily reports and incidents for this issue">
          {i.trend.length ? <VolumeChart data={i.trend} height={220} /> : <p className="text-sm text-muted">No data in range.</p>}
        </Card>
        <Card title="Score breakdown" subtitle="Deterministic weighted formula">
          <ScoreBreakdown issue={i} />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Common symptoms" subtitle="Observed in complaints (facts)">
          {i.symptoms.length ? (
            <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">{i.symptoms.map((s) => <li key={s}>{s}</li>)}</ul>
          ) : <p className="text-sm text-muted">None recorded.</p>}
          {i.keywords.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {i.keywords.map((k) => <span key={k} className="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-ink-2">{k}</span>)}
            </div>
          )}
        </Card>
        <Card title={<span className="flex items-center gap-1.5"><FlaskConical className="h-4 w-4 text-muted" /> Possible root causes</span>}
          subtitle="Unverified hypotheses - require investigation">
          {i.root_cause_hypotheses.length ? (
            <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">{i.root_cause_hypotheses.map((s) => <li key={s}>{s}</li>)}</ul>
          ) : (
            <Note>Root-cause hypotheses are generated only when the GPT-4o-mini labeler is enabled (OPENAI_API_KEY). None are invented without it.</Note>
          )}
        </Card>
      </div>

      <Card title="Representative complaints" subtitle="Closest to the cluster centroid · PII masked">
        <ul className="divide-y divide-[var(--border)]">
          {i.representative_complaints.map((c) => (
            <li key={c.complaint_id} className="py-3">
              <p className="text-sm text-ink">“{c.text}”</p>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                <SeverityBadge severity={c.severity} />
                <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: sourceColor(c.source) }} />{humanize(c.source)}</span>
                <span>{fmtDate(c.timestamp, true)}</span>
                {c.assignment_confidence !== null && <span>membership {Math.round((c.assignment_confidence ?? 0) * 100)}%</span>}
                {i.supporting_complaint_ids.includes(c.complaint_id) && <span className="font-medium text-ink-2">supports label</span>}
              </div>
              {c.classification_evidence && (
                <p className="mt-1 text-xs text-ink-2"><span className="text-muted">Evidence ({c.classification_source}):</span> {c.classification_evidence}</p>
              )}
            </li>
          ))}
        </ul>
        <Link to={{ pathname: "/complaints", search: `?issue_id=${i.issue_id}` }} className="mt-2 inline-block text-xs font-medium text-accent hover:underline">
          View all {fmtInt(i.total_reports)} complaints →
        </Link>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Severity mix"><SeverityBreakdown dist={i.severity_distribution} /></Card>
        <Card title="Reports by source"><CountBars rows={i.by_source} colorFor={(k) => sourceColor(k)} /></Card>
        <Card title="Classification evidence" subtitle="Category assigned to member complaints">
          <CountBars rows={i.evidence.category_distribution} colorFor={() => "var(--s1)"} />
        </Card>
      </div>

      <Card title="Demographic breakdown" subtitle={`Unique affected customers · segments with < ${Object.values(i.demographics)[0]?.min_customers ?? 3} customers suppressed for privacy · counts, not rates`}>
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {UI_DIMENSIONS.map(({ key: d }) => (
            <div key={d}>
              <h3 className="mb-2 text-xs font-semibold text-ink-2">{humanize(d)}</h3>
              {i.demographics[d] && <DemographicTable seg={i.demographics[d]} />}
            </div>
          ))}
        </div>
      </Card>
      <p className="text-[11px] text-muted">
        Created {fmtDate(i.created_at, true)} · updated {fmtDate(i.updated_at, true)}
      </p>
    </div>
  );
}
