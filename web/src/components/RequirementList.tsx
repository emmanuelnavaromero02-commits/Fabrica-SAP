import type { Requirement, Stage } from "../types";
import { StateBadge } from "./StateBadge";

interface Props {
  items: Requirement[];
  stages: Stage[];
  selected: number | null;
  onSelect: (id: number) => void;
}

export function RequirementList({ items, stages, selected, onSelect }: Props) {
  const label = (key: string) => stages.find((s) => s.key === key)?.label ?? key;

  if (items.length === 0) {
    return <p className="muted">Aún no hay requisitos. Crea el primero.</p>;
  }
  return (
    <ul className="req-list">
      {items.map((r) => (
        <li key={r.id}>
          <button
            className={r.id === selected ? "req-card active" : "req-card"}
            onClick={() => onSelect(r.id)}
          >
            <span className="req-id">#{r.id}</span>
            <span className="req-title">{r.title}</span>
            <span className="req-meta">
              {label(r.stage)} · {r.capability ?? "—"} {r.ricefw ?? ""}
            </span>
            <StateBadge state={r.state} />
          </button>
        </li>
      ))}
    </ul>
  );
}
