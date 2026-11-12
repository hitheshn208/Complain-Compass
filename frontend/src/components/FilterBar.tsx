import { Check, ChevronDown, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useFilters } from "../hooks/useFilters";
import { useApi } from "../hooks/useApi";
import { api } from "../services/api";
import { SEVERITIES, humanize } from "../lib/format";
import type { Filters } from "../types/api";

function MultiSelect({ label, options, value, onChange, format }: {
  label: string;
  options: string[];
  value: string[];
  onChange: (v: string[]) => void;
  format?: (v: string) => string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const active = value.length > 0;
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors ${
          active ? "border-accent bg-accent-wash text-accent-ink" : "border-line bg-surface text-ink-2 hover:bg-surface-2"
        }`}
        aria-expanded={open}
      >
        {label}
        {active && <span className="tabular rounded bg-accent px-1 text-[10px] text-white">{value.length}</span>}
        <ChevronDown className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div className="absolute left-0 z-30 mt-1 max-h-72 w-56 overflow-auto rounded-lg border border-line bg-surface p-1 shadow-xl">
          {options.length === 0 && <div className="px-2 py-1.5 text-xs text-muted">No values</div>}
          {options.map((o) => {
            const on = value.includes(o);
            return (
              <button
                key={o}
                type="button"
                onClick={() => onChange(on ? value.filter((v) => v !== o) : [...value, o])}
                className="flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-xs text-ink hover:bg-surface-2"
              >
                {format ? format(o) : o}
                {on && <Check className="h-4 w-4 stroke-[3] text-accent" />}
              </button>
            );
          })}
          {active && (
            <button type="button" onClick={() => onChange([])}
              className="mt-1 w-full border-t border-line px-2 py-1.5 text-left text-xs text-muted hover:text-ink">
              Clear {label.toLowerCase()}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

const DIMENSIONS: { key: keyof Filters; label: string }[] = [
  { key: "country", label: "Country" },
  { key: "state", label: "State" },
  { key: "age_group", label: "Age group" },
  { key: "student_status", label: "Student" },
  { key: "income_bracket", label: "Income" },
  { key: "source", label: "Source" },
];

/** One row of global filters above all analytics. Every control writes to the URL. */
export function FilterBar() {
  const { filters, setFilter, clear } = useFilters();
  const opts = useApi(() => api.filterOptions().then((r) => r.data), []);
  const anyActive = Object.values(filters).some((v) => (Array.isArray(v) ? v.length : v));

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-surface px-2 text-xs text-ink-2">
        From
        <input type="date" value={filters.date_from ?? ""} onChange={(e) => setFilter("date_from", e.target.value)}
          className="bg-transparent text-ink outline-none" />
      </label>
      <label className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-surface px-2 text-xs text-ink-2">
        To
        <input type="date" value={filters.date_to ?? ""} onChange={(e) => setFilter("date_to", e.target.value)}
          className="bg-transparent text-ink outline-none" />
      </label>
      {DIMENSIONS.map((d) => (
        <MultiSelect
          key={d.key}
          label={d.label}
          options={(opts.data?.[d.key as keyof typeof opts.data] as string[] | undefined) ?? []}
          value={(filters[d.key] as string[] | undefined) ?? []}
          onChange={(v) => setFilter(d.key, v)}
          format={d.key === "source" ? humanize : undefined}
        />
      ))}
      <MultiSelect label="Severity" options={[...SEVERITIES]} value={filters.severity ?? []}
        onChange={(v) => setFilter("severity", v)} format={humanize} />
      {anyActive && (
        <button onClick={clear} className="flex h-8 items-center gap-1 rounded-md px-2 text-xs font-medium text-ink-2 hover:bg-surface-2">
          <X className="h-3.5 w-3.5" /> Clear all
        </button>
      )}
    </div>
  );
}
