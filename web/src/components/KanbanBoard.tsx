import type { Requirement, Stage } from "../types";
import { STAGE_ICON } from "../stageIcons";
import { StateBadge } from "./StateBadge";

interface Props {
  items: Requirement[];
  stages: Stage[];
  selected: number | null;
  onOpen: (id: number) => void;
}


export function KanbanBoard({ items, stages, selected, onOpen }: Props) {
  if (!stages.length) return <p className="muted">Cargando etapas…</p>;
  const known = new Set(stages.map((s) => s.key));
  const orphans = items.filter((r) => !known.has(r.stage));
  const columns: Stage[] = orphans.length
    ? [...stages, { key: "__otras", label: "Otras etapas", kind: "auto", roles: [], next: null, on_reject: null }]
    : stages;
  return (
    <div className="kanban">
      {columns.map((stage) => {
        const column = stage.key === "__otras" ? orphans : items.filter((r) => r.stage === stage.key);
        if (stage.key === "desestimado" && column.length === 0) return null;
        return (
          <section key={stage.key} className="column" aria-label={stage.label}>
            <header className="column-head">
              <span>
                {STAGE_ICON[stage.kind]} {stage.label}
              </span>
              <span className="chip num">{column.length}</span>
            </header>
            {column.length === 0 && <p className="empty">Sin requisitos</p>}
            {column.map((r) => (
              <button
                key={r.id}
                className={r.id === selected ? "ticket selected" : "ticket"}
                onClick={() => onOpen(r.id)}
              >
                <span className="meta">
                  <span className="num">#{r.id}</span>
                  {r.capability && <span className="chip">{r.capability}</span>}
                  {r.ricefw && <span className="chip">RICEFW {r.ricefw}</span>}
                </span>
                <span className="title">{r.title}</span>
                <span className="meta">
                  <StateBadge state={r.state} />
                  <span className="num">IA ${r.spent_usd.toFixed(2)}</span>
                </span>
              </button>
            ))}
          </section>
        );
      })}
    </div>
  );
}
