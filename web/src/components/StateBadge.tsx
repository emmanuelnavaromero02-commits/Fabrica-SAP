import type { RunState } from "../types";

const LABELS: Record<RunState, string> = {
  running: "Agentes trabajando",
  waiting_gate: "Espera decisión",
  blocked: "En pausa",
  done: "Terminado",
};

export function StateBadge({ state }: { state: RunState }) {
  return (
    <span className={`status status-${state}`}>
      <span className="dot" aria-hidden />
      {LABELS[state]}
    </span>
  );
}
