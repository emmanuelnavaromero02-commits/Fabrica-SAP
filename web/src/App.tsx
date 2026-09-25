import { useEffect, useState } from "react";

import { api } from "./api";
import { IdentityBar } from "./components/IdentityBar";
import { NewRequirementForm } from "./components/NewRequirementForm";
import { RequirementList } from "./components/RequirementList";
import { RequirementView } from "./components/RequirementView";
import { usePolling, useStored } from "./hooks";
import type { Identity, Stage } from "./types";

export function App() {
  const [who, setWho] = useStored<Identity>("fabrica.identity", {
    user: "ana",
    role: "funcional",
  });
  const [selected, setSelected] = useStored<number | null>("fabrica.selected", null);
  const [stages, setStages] = useState<Stage[]>([]);
  const list = usePolling(() => api.list(who), 3000);

  useEffect(() => {
    api.stages(who).then(setStages, () => setStages([]));
  }, [who]);

  return (
    <div className="app">
      <header className="topbar">
        <h1>🏭 Fábrica SAP</h1>
        <span className="muted">beta · agentes con escalamiento N1→N4</span>
        <IdentityBar who={who} onChange={setWho} />
      </header>
      <main className="layout">
        <aside className="sidebar">
          <NewRequirementForm
            onCreate={async (data) => {
              const created = await api.create(who, data);
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
              who={who}
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
