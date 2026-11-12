import { Compass, FileUp, LayoutDashboard, ListOrdered, MessageSquareText, RefreshCw, Users } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { FilterBar } from "../components/FilterBar";
import { api } from "../services/api";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/issues", label: "Issues", icon: ListOrdered },
  { to: "/segments", label: "Segments", icon: Users },
  { to: "/complaints", label: "Complaints", icon: MessageSquareText },
  { to: "/upload", label: "Upload data", icon: FileUp },
];

export function AppLayout() {
  const { pathname, search } = useLocation();
  const showFilters = !pathname.startsWith("/upload");
  const [reclustering, setReclustering] = useState(false);
  const [msg, setMsg] = useState<string>();

  const recluster = async () => {
    setReclustering(true);
    setMsg(undefined);
    try {
      await api.recluster();
      setMsg("Reclustering started - refresh in a few seconds.");
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setReclustering(false);
    }
  };

  return (
    <div className="flex min-h-full">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-line bg-surface px-3 py-4 md:flex">
        <div className="mb-6 flex items-center gap-2 px-2">
          <Compass className="h-5 w-5 text-accent" />
          <span className="text-sm font-semibold tracking-tight">Complaint Compass</span>
        </div>
        <nav className="flex flex-col gap-0.5">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={{ pathname: to, search: to === "/upload" ? "" : search }}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors ${
                  isActive ? "bg-accent-wash font-medium text-accent-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink"
                }`
              }
            >
              <Icon className="h-4 w-4" /> {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto space-y-2 px-1">
          <button onClick={recluster} disabled={reclustering}
            className="flex w-full items-center justify-center gap-2 rounded-md border border-line px-2 py-1.5 text-xs font-medium text-ink-2 hover:bg-surface-2 disabled:opacity-50">
            <RefreshCw className={`h-3.5 w-3.5 ${reclustering ? "animate-spin" : ""}`} /> Re-run clustering
          </button>
          {msg && <p className="text-[11px] text-muted">{msg}</p>}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 border-b border-line bg-page/95 px-4 py-3 backdrop-blur sm:px-6">
          <div className="mb-2 flex items-center gap-3 md:hidden">
            <Compass className="h-5 w-5 text-accent" />
            <nav className="flex gap-1 overflow-x-auto">
              {NAV.map(({ to, label, end }) => (
                <NavLink key={to} to={{ pathname: to, search: to === "/upload" ? "" : search }} end={end}
                  className={({ isActive }) => `whitespace-nowrap rounded px-2 py-1 text-xs ${isActive ? "bg-accent-wash text-accent-ink" : "text-ink-2"}`}>
                  {label}
                </NavLink>
              ))}
            </nav>
          </div>
          {showFilters ? <FilterBar /> : <div className="text-sm font-medium text-ink-2">Data ingestion</div>}
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
