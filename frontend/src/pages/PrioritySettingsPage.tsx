import { ArrowDown, ArrowUp, RotateCcw, Save, Scale } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorState, Loading, Note } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useFilters } from "../hooks/useFilters";
import { api } from "../services/api";
import type { PriorityWeights } from "../types/api";

const COMPONENTS: { key: keyof PriorityWeights; label: string; help: string }[] = [
  { key: "impact", label: "Customer impact", help: "Unique affected customers (log scale), never raw message counts" },
  { key: "severity", label: "Severity", help: "How serious the complaints are: average plus the worst 10%" },
  { key: "recurrence", label: "Recurrence", help: "Customers hit more than once, and how many weeks it has been active" },
  { key: "growth", label: "Growth", help: "Last 7 days compared with the weeks before" },
  { key: "business", label: "Business impact", help: "Value of the category below, plus complaints mentioning money or orders" },
];

type Percents = Record<keyof PriorityWeights, number>;

const toPercents = (w: PriorityWeights): Percents =>
  Object.fromEntries(COMPONENTS.map(({ key }) => [key, Math.round(w[key] * 100)])) as Percents;

/** Scale the sliders proportionally so they add up to exactly 100. */
export function balance(p: Percents): Percents {
  const keys = COMPONENTS.map((c) => c.key);
  const total = keys.reduce((s, k) => s + p[k], 0);
  if (total === 0) return Object.fromEntries(keys.map((k) => [k, 20])) as Percents;
  const scaled = keys.map((k) => (p[k] * 100) / total);
  const out = scaled.map(Math.floor);
  // hand out the remaining points to the largest fractional parts; equal fractions (compared at 1e-6,
  // ignoring float noise) go to the larger weight, so sliders that were equal stay equal
  const rest = 100 - out.reduce((s, v) => s + v, 0);
  scaled.map((v, i) => ({ frac: Math.round((v - Math.floor(v)) * 1e6), v, i }))
    .sort((a, b) => b.frac - a.frac || b.v - a.v).slice(0, rest)
    .forEach(({ i }) => { out[i] += 1; });
  return Object.fromEntries(keys.map((k, i) => [k, out[i]])) as Percents;
}

function Slider({ value, onChange, label }: { value: number; onChange: (v: number) => void; label: string }) {
  return (
    <div className="flex items-center gap-3">
      <input type="range" min={0} max={100} step={1} value={value} aria-label={label}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-2 w-full cursor-pointer accent-[var(--accent)]" />
      <input type="number" min={0} max={100} value={value} aria-label={`${label} value`}
        onChange={(e) => onChange(Math.max(0, Math.min(100, Number(e.target.value) || 0)))}
        className="tabular h-7 w-16 rounded-md border border-line bg-surface px-1.5 text-right text-sm" />
    </div>
  );
}

export function PrioritySettingsPage() {
  const { filters, key } = useFilters();
  const settings = useApi(() => api.priority().then((r) => r.data), []);
  const top = useApi(() => api.top10(filters).then((r) => r.data), [key]);
  const [weights, setWeights] = useState<Percents>();
  const [cats, setCats] = useState<Record<string, number>>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; tone: "info" | "warn" }>();
  const [prevRanks, setPrevRanks] = useState<Map<string, number>>();

  // load the saved values into the form
  useEffect(() => {
    if (!settings.data) return;
    setWeights(toPercents(settings.data.weights));
    setCats({ ...settings.data.category_weights });
  }, [settings.data]);

  const total = weights ? COMPONENTS.reduce((s, { key: k }) => s + weights[k], 0) : 0;
  const saved = settings.data;
  const dirty = useMemo(() => {
    if (!saved || !weights || !cats) return false;
    const w = toPercents(saved.weights);
    return COMPONENTS.some(({ key: k }) => w[k] !== weights[k])
      || Object.keys(cats).some((c) => cats[c] !== saved.category_weights[c]);
  }, [saved, weights, cats]);

  const run = async (action: () => Promise<{ meta: Record<string, unknown> }>, done: string) => {
    setBusy(true);
    setMessage(undefined);
    setPrevRanks(new Map((top.data ?? []).map((i, n) => [i.issue_id, n + 1])));
    try {
      const res = await action();
      setMessage({ text: `${done} · ${res.meta.rescored_issues ?? 0} issues re-scored.`, tone: "info" });
      settings.reload();
      top.reload();
    } catch (e) {
      setMessage({ text: (e as Error).message, tone: "warn" });
    } finally {
      setBusy(false);
    }
  };

  const save = () => weights && cats && run(() => api.savePriority({
    weights: Object.fromEntries(COMPONENTS.map(({ key: k }) => [k, weights[k] / 100])) as unknown as PriorityWeights,
    category_weights: cats,
  }), "Saved");

  if (settings.error) return <ErrorState error={settings.error} onRetry={settings.reload} />;
  if (!saved || !weights || !cats) return <Loading label="Loading priority settings" />;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Priority settings</h1>
        <p className="text-sm text-muted">
          How issues are ranked. Saving re-scores every issue immediately; the AI is not involved.
          {saved.customized ? " Your company's custom settings are active." : " Using the default settings."}
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
        <div className="space-y-4">
          <Card title="Formula weights" subtitle="How much each factor counts. Must add up to 100%.">
            <div className="space-y-4">
              {COMPONENTS.map(({ key: k, label, help }) => (
                <div key={k}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-sm font-medium text-ink">{label}</span>
                    <span className="text-xs text-muted">default {Math.round(saved.defaults.weights[k] * 100)}%</span>
                  </div>
                  <p className="mb-1 text-xs text-muted">{help}</p>
                  <Slider label={label} value={weights[k]} onChange={(v) => setWeights({ ...weights, [k]: v })} />
                </div>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-surface-2 px-3 py-2 text-sm">
              <span className={total === 100 ? "text-ink" : "font-medium text-[var(--sev-critical)]"}>
                Total: <span className="tabular">{total}%</span>{total !== 100 && " (must be 100%)"}
              </span>
              {total !== 100 && (
                <button onClick={() => setWeights(balance(weights))}
                  className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1 text-xs font-medium hover:bg-surface">
                  <Scale className="h-3.5 w-3.5" /> Balance to 100%
                </button>
              )}
            </div>
            <p className="mt-2 text-xs text-muted">
              Priority = {COMPONENTS.map(({ key: k, label }) => `${weights[k]}% ${label}`).join(" + ")}
            </p>
          </Card>

          <Card title="Business value per category" subtitle="Used by the Business impact factor (0 = no value, 100 = most critical to the business).">
            <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
              {Object.keys(cats).map((c) => (
                <div key={c}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-sm text-ink">{c}</span>
                    <span className="text-xs text-muted">default {saved.defaults.category_weights[c]}</span>
                  </div>
                  <Slider label={c} value={cats[c]} onChange={(v) => setCats({ ...cats, [c]: v })} />
                </div>
              ))}
            </div>
          </Card>

          <div className="flex flex-wrap items-center gap-2">
            <button onClick={save} disabled={busy || total !== 100 || !dirty}
              className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40">
              <Save className="h-4 w-4" /> Save and re-rank
            </button>
            <button onClick={() => { setWeights(toPercents(saved.weights)); setCats({ ...saved.category_weights }); }}
              disabled={busy || !dirty}
              className="rounded-md border border-line px-3 py-1.5 text-sm font-medium text-ink-2 hover:bg-surface-2 disabled:opacity-40">
              Discard changes
            </button>
            <button onClick={() => run(() => api.resetPriority(), "Defaults restored")} disabled={busy || !saved.customized}
              className="flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-sm font-medium text-ink-2 hover:bg-surface-2 disabled:opacity-40">
              <RotateCcw className="h-4 w-4" /> Reset to defaults
            </button>
          </div>
          {message && <Note tone={message.tone}>{message.text}</Note>}
        </div>

        <Card title="Current top 10" subtitle={prevRanks ? "Arrows show the change since your last save" : "Updates when you save"}>
          {top.error && <ErrorState error={top.error} onRetry={top.reload} />}
          {!top.data ? !top.error && <Loading /> : top.data.length === 0 ? (
            <p className="text-sm text-muted">No issues yet: upload complaints first.</p>
          ) : (
            <ol className="space-y-1.5">
              {top.data.map((i, n) => {
                const before = prevRanks?.get(i.issue_id);
                const delta = before ? before - (n + 1) : 0;
                return (
                  <li key={i.issue_id} className="flex items-center gap-2 text-sm">
                    <span className="tabular w-5 shrink-0 text-right text-muted">{n + 1}</span>
                    <Link to={`/issues/${i.issue_id}`} className="min-w-0 flex-1 truncate text-ink hover:text-accent">{i.title}</Link>
                    {prevRanks && (before === undefined ? (
                      <span className="shrink-0 rounded bg-surface-2 px-1 text-[10px] font-medium text-ink-2">new</span>
                    ) : delta !== 0 && (
                      <span className={`flex shrink-0 items-center text-xs ${delta > 0 ? "text-[var(--good)]" : "text-[var(--sev-critical)]"}`}>
                        {delta > 0 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />}{Math.abs(delta)}
                      </span>
                    ))}
                    <span className="tabular shrink-0 text-xs text-muted">{i.priority_score?.toFixed(1)}</span>
                  </li>
                );
              })}
            </ol>
          )}
        </Card>
      </div>
    </div>
  );
}
