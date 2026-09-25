import { type ReactNode, useEffect, useMemo, useState } from "react";

import { api } from "./api";
import type { Session } from "./auth";
import { KanbanBoard } from "./components/KanbanBoard";
import { NewRequirementModal } from "./components/NewRequirementModal";
import { PortfolioView } from "./components/PortfolioView";
import { RequirementView } from "./components/RequirementView";
import { SessionGate } from "./components/SessionGate";
import { TimeView } from "./components/TimeView";
import { ThemeToggle } from "./components/ui/ThemeToggle";
import { ToastProvider, useToast } from "./components/ui/Toasts";
import { useActivityHeartbeat, usePolling, useStored } from "./hooks";
import type { Stage } from "./types";

type View = "tablero" | "requisito" | "portafolio" | "tiempo";

const SUPERVISORS = ["admin", "lider"];
const TITLES: Record<View, string> = {
  tablero: "Tablero de la fábrica",
  requisito: "Requisito",
  portafolio: "Portafolio",
  tiempo: "Tiempo trabajado",
};

function linkedRequirement(): number | null {
  const value = new URLSearchParams(window.location.search).get("requisito");
  return value ? Number(value) : null;
}

export function App() {
  return (
    <ToastProvider>
      <SessionGate>{(session, bar) => <Workspace session={session} bar={bar} />}</SessionGate>
    </ToastProvider>
  );
}

function Workspace({ session, bar }: { session: Session; bar: ReactNode }) {
  const [view, setView] = useState<View>(linkedRequirement() ? "requisito" : "tablero");
  const [selected, setSelected] = useStored<number | null>("fabrica.selected", null);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState("");
  const list = usePolling(() => api.list(session), 3000);
  const stagesPoll = usePolling(() => api.stages(session), 60_000);
  const stages: Stage[] = stagesPoll.data ?? [];
  const notify = useToast();
  const supervisor = SUPERVISORS.includes(session.role);

  useActivityHeartbeat(() => api.heartbeat(session, view === "requisito" ? selected : null));

  useEffect(() => {
    const linked = linkedRequirement();
    if (linked) setSelected(linked);
  }, []);

  const items = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = list.data ?? [];
    return q ? all.filter((r) => `#${r.id} ${r.title} ${r.capability ?? ""}`.toLowerCase().includes(q)) : all;
  }, [list.data, query]);

  const open = (id: number) => {
    setSelected(id);
    setView("requisito");
  };

  const navItems: { key: View; label: string; show: boolean }[] = [
    { key: "tablero", label: "🗂️ Tablero", show: true },
    { key: "requisito", label: "📄 Requisito abierto", show: selected !== null },
    { key: "portafolio", label: "📊 Portafolio", show: supervisor },
    { key: "tiempo", label: "⏱️ Tiempo", show: true },
  ];

  return (
    <div className="shell">
      <nav className="nav" aria-label="Principal">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            🏭
          </span>
          Fábrica SAP
        </div>
        {navItems
          .filter((n) => n.show)
          .map((n) => (
            <button key={n.key} className={view === n.key ? "active" : ""} onClick={() => setView(n.key)}>
              {n.label}
            </button>
          ))}
        <span className="spacer" />
        <div className="nav-foot">
          <ThemeToggle />
        </div>
      </nav>
      <div className="main">
        <header className="topbar">
          <h1>{TITLES[view]}</h1>
          {view === "tablero" && (
            <input
              className="search"
              placeholder="Buscar por número, título o módulo…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          )}
          <span className="grow" />
          <button onClick={() => setCreating(true)}>＋ Nuevo requisito</button>
          {bar}
        </header>
        {(list.error || (stagesPoll.error && !stagesPoll.data)) && (
          <p className="error page">No se pudo conectar con el API: {list.error ?? stagesPoll.error}</p>
        )}
        {view === "tablero" && (
          <section className="page">
            <KanbanBoard items={items} stages={stages} selected={selected} onOpen={open} />
          </section>
        )}
        {view === "requisito" && selected !== null && (
          <RequirementView
            key={selected}
            session={session}
            id={selected}
            stages={stages}
            onBack={() => setView("tablero")}
            onChanged={() => void list.refresh()}
          />
        )}
        {view === "portafolio" && supervisor && <PortfolioView session={session} stages={stages} onOpen={open} />}
        {view === "tiempo" && <TimeView session={session} />}
      </div>
      {creating && (
        <NewRequirementModal
          onClose={() => setCreating(false)}
          onCreate={async (data, files) => {
            const created = files.length
              ? await api.createWithFiles(session, data, files)
              : await api.create(session, data);
            notify(`Requisito #${created.id} enviado a la fábrica`);
            await list.refresh();
            open(created.id);
          }}
        />
      )}
    </div>
  );
}
