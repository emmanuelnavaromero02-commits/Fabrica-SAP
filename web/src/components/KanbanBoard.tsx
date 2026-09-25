import type { Priority, Requirement, Stage } from "../types";
import { STAGE_ICON } from "../stageIcons";
import { StateBadge } from "./StateBadge";

interface Props {
  items: Requirement[];
  stages: Stage[];
  selected: number | null;
  onOpen: (id: number) => void;
}

const PRIORITY_STYLE: Record<Priority, { label: string; color: string; bg: string }> = {
  urgente: { label: "🔥 Urgente", color: "#ef4444", bg: "rgba(239, 68, 68, 0.15)" },
  alta: { label: "⚡ Alta", color: "#f97316", bg: "rgba(249, 115, 22, 0.15)" },
  media: { label: "🔷 Media", color: "#3b82f6", bg: "rgba(59, 130, 246, 0.15)" },
  baja: { label: "☕ Baja", color: "#94a3b8", bg: "rgba(148, 163, 184, 0.15)" },
};

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
            {column.map((r) => {
              const pBadge = r.priority && r.priority in PRIORITY_STYLE ? PRIORITY_STYLE[r.priority] : null;
              return (
                <button
                  key={r.id}
                  className={r.id === selected ? "ticket selected" : "ticket"}
                  onClick={() => onOpen(r.id)}
                >
                  <span className="meta">
                    <span className="num">#{r.id}</span>
                    {pBadge && (
                      <span
                        className="chip"
                        style={{
                          background: pBadge.bg,
                          color: pBadge.color,
                          fontWeight: 600,
                        }}
                      >
                        {pBadge.label}
                      </span>
                    )}
                  {r.capability && <span className="chip">{r.capability}</span>}
                  {r.ricefw && <span className="chip">RICEFW {r.ricefw}</span>}
                  {r.holder_user ? (
                    <span className="chip" style={{ background: "rgba(37,99,235,0.15)", color: "var(--primary)" }}>👤 {r.holder_user}</span>
                  ) : (
                    <span className="chip">👥 Pool</span>
                  )}
                  {r.due_date && (
                    <span className="chip" title={`SLA: ${new Date(r.due_date).toLocaleDateString()}`}>
                      📅 {new Date(r.due_date).toLocaleDateString()}
                    </span>
                  )}
                </span>
                <span className="title">{r.title}</span>
                <span className="meta">
                  <StateBadge state={r.state} />
                  <span className="num">IA ${r.spent_usd.toFixed(2)}</span>
                </span>
              </button>
            );
          })}
          </section>
        );
      })}
    </div>
  );
}
