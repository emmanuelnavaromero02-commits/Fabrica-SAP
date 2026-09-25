import { type ReactNode, useEffect, useState } from "react";

import { api } from "./api";
import type { Session } from "./auth";
import { NewRequirementForm } from "./components/NewRequirementForm";
import { RequirementList } from "./components/RequirementList";
import { RequirementView } from "./components/RequirementView";
import { SessionGate } from "./components/SessionGate";
import { usePolling, useStored } from "./hooks";
import type { Stage } from "./types";

export function App() {
  return <SessionGate>{(session, bar) => <Workspace session={session} bar={bar} />}</SessionGate>;
}

function Workspace({ session, bar }: { session: Session; bar: ReactNode }) {
  const [selected, setSelected] = useStored<number | null>("fabrica.selected", null);
  const [stages, setStages] = useState<Stage[]>([]);
  const list = usePolling(() => api.list(session), 3000);

  useEffect(() => {
    api.stages(session).then(setStages, () => setStages([]));
  }, [session.user, session.role]);

  return (
    <div className="app">
      <header className="topbar">
        <h1>🏭 Fábrica SAP</h1>
        <span className="muted">escalamiento N1→N4</span>
        {bar}
      </header>
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
          <RequirementList
            items={list.data ?? []}
            stages={stages}
            selected={selected}
            onSelect={setSelected}
          />
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
    </div>
  );
}
