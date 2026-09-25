import { useState } from "react";

import { api } from "../api";
import type { Session } from "../auth";
import { usePolling } from "../hooks";
import type { Outcome, Priority, Stage, StandardSize } from "../types";
import { Conversation } from "./detail/Conversation";
import { Deliverables } from "./detail/Deliverables";
import { EscalationLadder } from "./detail/EscalationLadder";
import { SapPanel } from "./detail/SapPanel";
import { Stepper } from "./detail/Stepper";
import { Summary } from "./detail/Summary";
import { DocumentsPanel } from "./DocumentsPanel";
import { GateActions } from "./GateActions";
import { StateBadge } from "./StateBadge";
import { errorText } from "../errors";
import { useToast } from "./ui/Toasts";

type Tab = "resumen" | "conversacion" | "entregables" | "escalamiento" | "documentos" | "sap";

interface Props {
  session: Session;
  id: number;
  stages: Stage[];
  onBack: () => void;
  onChanged: () => void;
}

export function RequirementView({ session, id, stages, onBack, onChanged }: Props) {
  const [tab, setTab] = useState<Tab>("resumen");
  const notify = useToast();
  const { data, error, refresh } = usePolling(() => api.detail(session, id), 2000);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Cargando…</p>;

  const req = data.requirement;
  const stage = stages.find((s) => s.key === req.stage);
  const openQuestions = data.messages.filter((m) => m.kind === "pregunta" && !m.resolved).length;
  const after = async (action: Promise<void>, done: string) => {
    try {
      await action;
      notify(done);
      await refresh();
      onChanged();
    } catch (e) {
      notify(errorText(e), "error");
      throw e;
    }
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: "resumen", label: "Resumen" },
    { key: "conversacion", label: `Conversación${openQuestions ? ` (${openQuestions} ❓)` : ""}` },
    { key: "entregables", label: `Entregables (${new Set(data.artifacts.map((a) => a.path)).size})` },
    { key: "escalamiento", label: "Escalamiento" },
    { key: "documentos", label: "Documentos" },
    { key: "sap", label: "SAP" },
  ];

  return (
    <section className="page">
      <div className="headline">
        <button className="ghost" onClick={onBack}>
          ← Tablero
        </button>
        <h2>
          #{req.id} {req.title}
        </h2>
        <StateBadge state={req.state} />
        <span className="chip num">IA ${req.spent_usd.toFixed(4)}</span>
        <a
          className="button outline"
          href={api.dossierUrl(req.id, session)}
          target="_blank"
          rel="noreferrer"
          style={{ textDecoration: "none", fontSize: "12px", padding: "4px 10px", borderRadius: "6px" }}
        >
          📄 Acta Técnica
        </a>
      </div>
      <div className="card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 16px", marginBottom: "12px" }}>
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          <span>⚽ <strong>Pelota:</strong></span>
          {req.holder_user ? (
            <span className="chip">👤 Asignado a: <strong>{req.holder_user}</strong></span>
          ) : (
            <span className="chip">👥 En pool: <strong>{req.holder_role || stage?.label || "equipo"}</strong></span>
          )}
        </div>
        <div className="gate-buttons">
          {!req.holder_user && (
            <button style={{ padding: "4px 10px" }} onClick={() => after(api.claim(session, id), "Requisito tomado")}>
              ✋ Tomar del pool
            </button>
          )}
          {req.holder_user === session.user && (
            <>
              <button
                className="outline"
                style={{ padding: "4px 10px" }}
                onClick={() => {
                  const target = window.prompt("Usuario destinatario:");
                  if (target) after(api.transfer(session, id, target), `Transferido a ${target}`);
                }}
              >
                Pasar pelota
              </button>
              <button
                className="ghost"
                style={{ padding: "4px 10px" }}
                onClick={() => after(api.release(session, id), "Devuelto al pool")}
              >
                Devolver al pool
              </button>
            </>
          )}
        </div>
      </div>
      <div className="card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 16px", marginBottom: "12px", flexWrap: "wrap", gap: "10px" }}>
        <div style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px" }}>
            <span>Prioridad:</span>
            <select
              value={req.priority || "media"}
              onChange={(e) =>
                after(
                  api.setPriority(session, id, e.target.value as Priority, req.due_date),
                  "Prioridad actualizada"
                )
              }
            >
              <option value="urgente">🔥 Urgente</option>
              <option value="alta">⚡ Alta</option>
              <option value="media">🔷 Media</option>
              <option value="baja">☕ Baja</option>
            </select>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px" }}>
            <span>SLA / Entrega:</span>
            <input
              type="date"
              value={req.due_date ? req.due_date.slice(0, 10) : ""}
              onChange={(e) =>
                after(
                  api.setPriority(
                    session,
                    id,
                    req.priority,
                    e.target.value ? new Date(e.target.value).toISOString() : null
                  ),
                  "Fecha límite actualizada"
                )
              }
            />
          </label>
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px" }}>
            <span>Tamaño RICEFW:</span>
            <select
              defaultValue=""
              onChange={(e) => {
                if (e.target.value) {
                  after(api.setSize(session, id, e.target.value as StandardSize), `Dimensionado a ${e.target.value}`);
                  e.target.value = "";
                }
              }}
            >
              <option value="" disabled>Seleccionar estándar…</option>
              <option value="tiny">Tiny (XS · 8h)</option>
              <option value="small">Small (S · 24h)</option>
              <option value="medium">Medium (M · 40h)</option>
              <option value="large">Large (L · 80h)</option>
              <option value="very_large">Very Large (XL · 120h)</option>
            </select>
          </label>
        </div>
      </div>
      <Stepper stages={stages} current={req.stage} messages={data.messages} />
      <GateActions
        session={session}
        requirement={req}
        stage={stage}
        onDecide={(outcome: Outcome, comment: string) =>
          after(api.decide(session, id, outcome, comment), "Decisión registrada")
        }
        onResume={() => after(api.resume(session, id), "Etapa reanudada")}
      />
      <nav className="tabs" role="tablist">
        {tabs.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={t.key === tab}
            className={t.key === tab ? "active" : ""}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {tab === "resumen" && <Summary data={data} />}
      {tab === "conversacion" && (
        <Conversation
          messages={data.messages}
          onAnswer={(messageId, body) =>
            after(api.answer(session, id, messageId, body), "Respuesta enviada a la fábrica")
          }
        />
      )}
      {tab === "entregables" && (
        <Deliverables session={session} id={id} artifacts={data.artifacts} repoUrl={req.repo_url} />
      )}
      {tab === "escalamiento" && <EscalationLadder attempts={data.attempts} />}
      {tab === "documentos" && <DocumentsPanel session={session} id={id} />}
      {tab === "sap" && <SapPanel transports={data.transports} calls={data.sap_calls} />}
    </section>
  );
}
