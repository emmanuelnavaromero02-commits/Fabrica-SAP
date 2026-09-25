import type { RunState } from "../types";

const LABELS: Record<RunState, string> = {
  running: "Agentes trabajando",
  waiting_gate: "Espera decisión",
  blocked: "Bloqueado",
  done: "Terminado",
};

export function StateBadge({ state }: { state: RunState }) {
  return <span className={`badge badge-${state}`}>{LABELS[state]}</span>;
}
