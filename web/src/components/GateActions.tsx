import { useState } from "react";

import type { Session } from "../auth";
import type { Outcome, Requirement, Stage } from "../types";

interface Props {
  session: Session;
  requirement: Requirement;
  stage: Stage | undefined;
  onDecide: (outcome: Outcome, comment: string) => Promise<void>;
  onResume: () => Promise<void>;
}

const DISCARD_ROLES = ["admin", "lider"];

export function GateActions({ session, requirement, stage, onDecide, onResume }: Props) {
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  const run = async (action: () => Promise<void>) => {
    setError(null);
    try {
      await action();
      setComment("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const isGate = requirement.state === "waiting_gate" && stage?.kind === "gate";
  const canDecide = isGate && stage.roles.includes(session.role);
  const canDiscard = requirement.state !== "done" && DISCARD_ROLES.includes(session.role);
  const blocked = requirement.state === "blocked";

  if (!isGate && !canDiscard && !blocked) return null;

  return (
    <div className="card gate">
      {isGate && canDecide && (
        <strong>Te toca decidir en {stage.label}</strong>
      )}
      {blocked && <strong>⛔ La etapa está en pausa: revisa la conversación y reintenta</strong>}
      {isGate && !canDecide && (
        <p className="muted">
          Espera decisión de: <strong>{stage.roles.join(" o ")}</strong>
        </p>
      )}
      {(canDecide || canDiscard) && (
        <input
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Comentario de la decisión (opcional)"
        />
      )}
      <div className="gate-buttons">
        {canDecide && (
          <>
            <button onClick={() => run(() => onDecide("approve", comment))}>✅ Aprobar</button>
            <button className="outline" onClick={() => run(() => onDecide("reject", comment))}>
              ↩️ Devolver
            </button>
          </>
        )}
        {blocked && (
          <button className="outline" onClick={() => run(onResume)}>
            ▶ Reintentar etapa
          </button>
        )}
        {canDiscard && (
          <button className="danger" onClick={() => run(() => onDecide("discard", comment))}>
            🚫 Desestimar
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
