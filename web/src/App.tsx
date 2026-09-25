import { type ReactNode, useEffect, useState } from "react";

import { api } from "./api";
import type { Session } from "./auth";
import { NewRequirementForm } from "./components/NewRequirementForm";
import { PortfolioView } from "./components/PortfolioView";
import { RequirementList } from "./components/RequirementList";
import { RequirementView } from "./components/RequirementView";
import { SessionGate } from "./components/SessionGate";
import { TimeView } from "./components/TimeView";
import { useActivityHeartbeat, usePolling, useStored } from "./hooks";
import type { Stage } from "./types";

type View = "requisitos" | "portafolio" | "tiempo";

const SUPERVISORS = ["admin", "lider"];

function linkedRequirement(): number | null {
  const value = new URLSearchParams(window.location.search).get("requisito");
  return value ? Number(value) : null;
}

export function App() {
  return <SessionGate>{(session, bar) => <Workspace session={session} bar={bar} />}</SessionGate>;
}

function Workspace({ session, bar }: { session: Session; bar: ReactNode }) {
  const [view, setView] = useState<View>("requisitos");
  const [selected, setSelected] = useStored<number | null>("fabrica.selected", null);
  const [stages, setStages] = useState<Stage[]>([]);
  const list = usePolling(() => api.list(session), 3000);
  const supervisor = SUPERVISORS.includes(session.role);

  useActivityHeartbeat(() => api.heartbeat(session, view === "requisitos" ? selected : null));

  useEffect(() => {
    const linked = linkedRequirement();
    if (linked) setSelected(linked);
  }, []);

  useEffect(() => {
    api.stages(session).then(setStages, () => setStages([]));
  }, [session.user, session.role]);

  const open = (id: number) => {
    setSelected(id);
    setView("requisitos");
  };

  return (
    <div className="app">
      <header className="topbar">
        <h1>🏭 Fábrica SAP</h1>
        <nav className="views">
          <button className={view === "requisitos" ? "tab active" : "tab"} onClick={() => setView("requisitos")}>
            Requisitos
          </button>
          {supervisor && (
            <button className={view === "portafolio" ? "tab active" : "tab"} onClick={() => setView("portafolio")}>
              Portafolio
            </button>
          )}
          <button className={view === "tiempo" ? "tab active" : "tab"} onClick={() => setView("tiempo")}>
            Tiempo
          </button>
        </nav>
        {bar}
      </header>
      {view === "portafolio" && supervisor && (
        <main className="single">
          <PortfolioView session={session} onOpen={open} />
        </main>
      )}
      {view === "tiempo" && (
        <main className="single">
          <TimeView session={session} />
        </main>
      )}
      {view === "requisitos" && (
        <main className="layout">
          <aside className="sidebar">
            <NewRequirementForm
              onCreate={async (data, files) => {
                const created = files.length
                  ? await api.createWithFiles(session, data, files)
                  : await api.create(session, data);
                setSelected(created.id);
                await list.refresh();
              }}
            />
            {list.error && <p className="error">API no disponible: {list.error}</p>}
            <RequirementList items={list.data ?? []} stages={stages} selected={selected} onSelect={setSelected} />
          </aside>
          <section className="content">
            {selected ? (
              <RequirementView
                key={selected}
                session={session}
                id={selected}
                stages={stages}
                onChanged={() => void list.refresh()}
              />
            ) : (
              <p className="muted">Selecciona un requisito o crea uno nuevo.</p>
            )}
          </section>
        </main>
      )}
    </div>
  );
}
