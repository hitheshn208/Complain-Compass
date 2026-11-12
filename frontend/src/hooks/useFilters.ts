import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { Filters } from "../types/api";

export const LIST_KEYS = ["country", "state", "age_group", "student_status", "income_bracket", "source", "severity"] as const;
export type ListKey = (typeof LIST_KEYS)[number];

export function parseFilters(params: URLSearchParams): Filters {
  const f: Filters = {};
  const from = params.get("date_from");
  const to = params.get("date_to");
  if (from) f.date_from = from;
  if (to) f.date_to = to;
  for (const key of LIST_KEYS) {
    const raw = params.get(key);
    if (raw) f[key] = raw.split(",").filter(Boolean);
  }
  return f;
}

export function activeFilterCount(f: Filters): number {
  return (f.date_from ? 1 : 0) + (f.date_to ? 1 : 0) + LIST_KEYS.reduce((n, k) => n + (f[k]?.length ? 1 : 0), 0);
}

/** Global dashboard filters, synced to the URL so views are shareable and survive reloads. */
export function useFilters() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params), [params]);
  const key = params.toString();

  const setFilter = useCallback(
    (name: keyof Filters, value: string | string[] | undefined) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          const v = Array.isArray(value) ? value.join(",") : value;
          if (v) next.set(name, v);
          else next.delete(name);
          next.delete("page");
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const clear = useCallback(() => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const k of [...LIST_KEYS, "date_from", "date_to", "page"]) next.delete(k);
        return next;
      },
      { replace: true },
    );
  }, [setParams]);

  return { filters, setFilter, clear, key };
}
