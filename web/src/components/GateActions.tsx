import { useState } from "react";

import type { Identity, Outcome, Requirement, Stage } from "../types";

interface Props {
  who: Identity;
  requirement: Requirement;
  stage: Stage | undefined;
  onDecide: (outcome: Outcome, comment: string) => Promise<void>;
  onResume: () => Promise<void>;
}

const DISCARD_ROLES = ["admin", "lider"];

export function GateActions({ who, requirement, stage, onDecide, onResume }: Props) {
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
  const canDecide = isGate && stage.roles.includes(who.role);
  const canDiscard = requirement.state !== "done" && DISCARD_ROLES.includes(who.role);
  const blocked = requirement.state === "blocked";

  if (!isGate && !canDiscard && !blocked) return null;

  return (
    <div className="gate">
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
            <button className="secondary" onClick={() => run(() => onDecide("reject", comment))}>
              ↩️ Devolver
            </button>
          </>
        )}
        {blocked && (
          <button className="secondary" onClick={() => run(onResume)}>
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
