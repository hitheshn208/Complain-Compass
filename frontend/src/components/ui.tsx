import { AlertTriangle, Info, Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { SEVERITY_COLOR, humanize } from "../lib/format";

export function Card({ title, subtitle, action, children, className = "" }: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-line bg-surface p-4 sm:p-5 ${className}`}>
      {(title || action) && (
        <header className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function StatTile({ label, value, hint, icon, tone }: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  tone?: "default" | "alert";
}) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-center gap-2 text-xs font-medium text-ink-2">
        {icon && <span className={tone === "alert" ? "text-[var(--sev-critical)]" : "text-muted"}>{icon}</span>}
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold tracking-tight text-ink">{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function SeverityBadge({ severity }: { severity: string | null | undefined }) {
  if (!severity) return <span className="text-xs text-muted">Unrated</span>;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line px-2 py-0.5 text-xs font-medium text-ink">
      <span className="h-2 w-2 rounded-full" style={{ background: SEVERITY_COLOR[severity] }} aria-hidden />
      {humanize(severity)}
    </span>
  );
}

export function StatusPill({ status }: { status: string }) {
  return (
    <span className="rounded-md bg-surface-2 px-2 py-0.5 text-xs font-medium text-ink-2">{humanize(status)}</span>
  );
}

export function PriorityMeter({ value }: { value: number | null | undefined }) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-2" aria-hidden>
        <div className="h-full rounded-full bg-accent" style={{ width: `${v}%` }} />
      </div>
      <span className="tabular text-sm font-semibold text-ink">{(value ?? 0).toFixed(1)}</span>
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted">
      <Loader2 className="h-4 w-4 animate-spin" /> {label}…
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-line bg-surface py-10 text-center">
      <AlertTriangle className="h-5 w-5 text-[var(--sev-critical)]" />
      <div className="text-sm text-ink">{error.message}</div>
      {onRetry && (
        <button onClick={onRetry} className="rounded-md border border-line px-3 py-1.5 text-xs font-medium hover:bg-surface-2">
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-center">
      <div className="text-sm font-medium text-ink">{title}</div>
      {children && <div className="max-w-md text-xs text-muted">{children}</div>}
    </div>
  );
}

export function Note({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" }) {
  return (
    <div className="flex items-start gap-2 rounded-lg bg-surface-2 px-3 py-2 text-xs text-ink-2">
      {tone === "warn" ? (
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--sev-high)]" />
      ) : (
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" />
      )}
      <div>{children}</div>
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: i.color }} aria-hidden />
          {i.label}
        </li>
      ))}
    </ul>
  );
}

export function Pagination({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  if (pages <= 1) return null;
  return (
    <div className="flex items-center justify-end gap-2 pt-3 text-xs text-ink-2">
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}
        className="rounded-md border border-line px-2.5 py-1 disabled:opacity-40 hover:bg-surface-2">Previous</button>
      <span className="tabular">Page {page} of {pages}</span>
      <button disabled={page >= pages} onClick={() => onPage(page + 1)}
        className="rounded-md border border-line px-2.5 py-1 disabled:opacity-40 hover:bg-surface-2">Next</button>
    </div>
  );
}
