import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipProps } from "recharts";
import { useNavigate } from "react-router-dom";
import type { CountRow, IssueSummary, SeriesPoint } from "../types/api";
import { SEVERITIES, SEVERITY_COLOR, fmtDate, fmtInt, humanize, truncate } from "../lib/format";
import { Legend } from "./ui";

const AXIS = { stroke: "var(--axis)", tickLine: false, fontSize: 11 } as const;

function TooltipBox({ title, rows }: { title: string; rows: { label: string; value: string; color?: string }[] }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 font-medium text-ink">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between gap-4 text-ink-2">
          <span className="flex items-center gap-1.5">
            {r.color && <span className="h-2 w-2 rounded-sm" style={{ background: r.color }} />}
            {r.label}
          </span>
          <span className="tabular font-medium text-ink">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

/** Complaint volume over time: reports vs incidents (duplicates collapsed). One axis, one unit. */
export function VolumeChart({ data, height = 240 }: { data: SeriesPoint[]; height?: number }) {
  const tip = ({ active, payload, label }: TooltipProps<number, string>) => {
    if (!active || !payload?.length) return null;
    const p = payload[0].payload as SeriesPoint;
    return (
      <TooltipBox
        title={fmtDate(label as string)}
        rows={[
          { label: "Reports", value: fmtInt(p.reports), color: "var(--s1)" },
          { label: "Incidents", value: fmtInt(p.incidents), color: "var(--s2)" },
          { label: "Unique customers", value: fmtInt(p.unique_customers) },
          ...SEVERITIES.map((s) => ({ label: humanize(s), value: fmtInt(p[s]), color: SEVERITY_COLOR[s] })),
        ]}
      />
    );
  };
  return (
    <div>
      <div className="mb-2">
        <Legend items={[{ label: "Reports", color: "var(--s1)" }, { label: "Incidents (duplicates collapsed)", color: "var(--s2)" }]} />
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
          <defs>
            <linearGradient id="volFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--s1)" stopOpacity={0.18} />
              <stop offset="100%" stopColor="var(--s1)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} strokeDasharray="0" />
          <XAxis dataKey="period" {...AXIS} tickFormatter={(v) => fmtDate(v).replace(/, \d{4}$/, "")} minTickGap={24} />
          <YAxis {...AXIS} axisLine={false} allowDecimals={false} width={40} />
          <Tooltip content={tip} cursor={{ stroke: "var(--axis)", strokeWidth: 1 }} />
          <Area type="monotone" dataKey="reports" stroke="var(--s1)" strokeWidth={2} fill="url(#volFill)" dot={false}
            activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
          <Area type="monotone" dataKey="incidents" stroke="var(--s2)" strokeWidth={2} fill="none" dot={false}
            activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Severity distribution as a single segmented bar + labeled rows (status colors always paired with labels). */
export function SeverityBreakdown({ dist }: { dist: Record<string, number> }) {
  const total = SEVERITIES.reduce((n, s) => n + (dist[s] ?? 0), 0);
  const unclassified = dist.UNCLASSIFIED ?? 0;
  return (
    <div className="space-y-4">
      <div className="flex h-3 w-full gap-[2px] overflow-hidden rounded-full bg-surface-2" role="img"
        aria-label={SEVERITIES.map((s) => `${s} ${dist[s] ?? 0}`).join(", ")}>
        {total > 0 &&
          SEVERITIES.map((s) =>
            dist[s] ? <div key={s} style={{ width: `${(100 * dist[s]) / total}%`, background: SEVERITY_COLOR[s] }} title={`${humanize(s)}: ${dist[s]}`} /> : null,
          )}
      </div>
      <ul className="space-y-2">
        {SEVERITIES.map((s) => (
          <li key={s} className="flex items-center justify-between text-sm">
            <span className="flex items-center gap-2 text-ink-2">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: SEVERITY_COLOR[s] }} />
              {humanize(s)}
            </span>
            <span className="tabular text-ink">
              {fmtInt(dist[s] ?? 0)} <span className="text-muted">({total ? Math.round((100 * (dist[s] ?? 0)) / total) : 0}%)</span>
            </span>
          </li>
        ))}
      </ul>
      {unclassified > 0 && <p className="text-xs text-muted">{fmtInt(unclassified)} complaint(s) not yet classified.</p>}
    </div>
  );
}

/** Horizontal priority bars for the top issues; click navigates to the issue. */
export function PriorityBars({ issues }: { issues: IssueSummary[] }) {
  const navigate = useNavigate();
  const data = issues.map((i) => ({ ...i, label: truncate(i.title, 38) }));
  const tip = ({ active, payload }: TooltipProps<number, string>) => {
    if (!active || !payload?.length) return null;
    const i = payload[0].payload as IssueSummary;
    return (
      <TooltipBox
        title={i.title}
        rows={[
          { label: "Priority", value: (i.priority_score ?? 0).toFixed(1) },
          { label: "Severity", value: humanize(i.severity), color: i.severity ? SEVERITY_COLOR[i.severity] : undefined },
          { label: "Unique customers", value: fmtInt(i.unique_affected_customers) },
          { label: "Reports", value: fmtInt(i.total_reports) },
        ]}
      />
    );
  };
  return (
    <ResponsiveContainer width="100%" height={Math.max(160, data.length * 34)}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, left: 0, bottom: 0 }} barCategoryGap={8}>
        <CartesianGrid horizontal={false} />
        <XAxis type="number" domain={[0, 100]} {...AXIS} />
        <YAxis type="category" dataKey="label" width={210} {...AXIS} axisLine={false} interval={0}
          tick={{ fill: "var(--ink-2)", fontSize: 11 }} />
        <Tooltip content={tip} />
        <Bar dataKey="priority_score" radius={[0, 4, 4, 0]} maxBarSize={18} cursor="pointer"
          onClick={(d: { issue_id?: string }) => d.issue_id && navigate(`/issues/${d.issue_id}`)}>
          {data.map((d) => (
            <Cell key={d.issue_id} fill="var(--s1)" />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Generic count bars for a categorical breakdown (sources, countries, segments). */
export function CountBars({ rows, colorFor, valueKey = "reports", valueLabel = "Reports", height }: {
  rows: CountRow[];
  colorFor?: (key: string, index: number) => string;
  valueKey?: "reports" | "unique_customers";
  valueLabel?: string;
  height?: number;
}) {
  const tip = ({ active, payload }: TooltipProps<number, string>) => {
    if (!active || !payload?.length) return null;
    const r = payload[0].payload as CountRow;
    return (
      <TooltipBox title={humanize(r.key)} rows={[
        { label: "Reports", value: fmtInt(r.reports) },
        { label: "Unique customers", value: fmtInt(r.unique_customers) },
      ]} />
    );
  };
  return (
    <ResponsiveContainer width="100%" height={height ?? Math.max(140, rows.length * 30)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 16, left: 0, bottom: 0 }} barCategoryGap={6}>
        <CartesianGrid horizontal={false} />
        <XAxis type="number" {...AXIS} allowDecimals={false} name={valueLabel} />
        <YAxis type="category" dataKey="key" width={110} {...AXIS} axisLine={false} interval={0}
          tickFormatter={(v) => truncate(humanize(v), 16)} tick={{ fill: "var(--ink-2)", fontSize: 11 }} />
        <Tooltip content={tip} />
        <Bar dataKey={valueKey} radius={[0, 4, 4, 0]} maxBarSize={16}>
          {rows.map((r, i) => (
            <Cell key={r.key} fill={colorFor ? colorFor(r.key, i) : "var(--s1)"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
