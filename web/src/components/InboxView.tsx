import { useState } from "react";
import { api } from "../api";
import type { Session } from "../auth";
import { errorText } from "../errors";
import { usePolling } from "../hooks";
import type { Priority, Requirement } from "../types";
import { StateBadge } from "./StateBadge";
import { useToast } from "./ui/Toasts";

const PRIORITY_STYLE: Record<Priority, { label: string; color: string; bg: string }> = {
  urgente: { label: "🔥 Urgente", color: "#ef4444", bg: "rgba(239, 68, 68, 0.15)" },
  alta: { label: "⚡ Alta", color: "#f97316", bg: "rgba(249, 115, 22, 0.15)" },
  media: { label: "🔷 Media", color: "#3b82f6", bg: "rgba(59, 130, 246, 0.15)" },
  baja: { label: "☕ Baja", color: "#94a3b8", bg: "rgba(148, 163, 184, 0.15)" },
};

interface Props {
  session: Session;
  onOpen: (id: number) => void;
  onChanged: () => void;
}

export function InboxView({ session, onOpen, onChanged }: Props) {
  const { data, error, refresh } = usePolling(() => api.inbox(session), 3000);
  const notify = useToast();
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [submitting, setSubmitting] = useState<Record<number, boolean>>({});

  const perform = async (action: Promise<void>, msg: string) => {
    try {
      await action;
      notify(msg);
      await refresh();
      onChanged();
    } catch (e) {
      notify(errorText(e), "error");
    }
  };

  const submitAnswer = async (reqId: number, messageId: number) => {
    const text = (answers[messageId] || "").trim();
    if (!text) return;
    setSubmitting((prev) => ({ ...prev, [messageId]: true }));
    try {
      await api.answer(session, reqId, messageId, text);
      notify("Respuesta enviada a la fábrica");
      setAnswers((prev) => {
        const next = { ...prev };
        delete next[messageId];
        return next;
      });
      await refresh();
      onChanged();
    } catch (e) {
      notify(errorText(e), "error");
    } finally {
      setSubmitting((prev) => ({ ...prev, [messageId]: false }));
    }
  };

  if (error) return <p className="error page">Error cargando bandeja: {error}</p>;
  if (!data) return <p className="muted page">Cargando bandeja de trabajo…</p>;

  const { assigned, pool, waiting_gates, open_questions } = data;

  return (
    <section className="page" style={{ display: "grid", gap: "20px" }}>
      <div className="tiles">
        <div className="tile">
          <span className="label">👤 Asignados a ti</span>
          <span className="value" style={{ color: assigned.length > 0 ? "var(--primary)" : "inherit" }}>
            {assigned.length}
          </span>
        </div>
        <div className="tile">
          <span className="label">🚦 Compuertas pendientes</span>
          <span className="value" style={{ color: waiting_gates.length > 0 ? "var(--warning)" : "inherit" }}>
            {waiting_gates.length}
          </span>
        </div>
        <div className="tile">
          <span className="label">❓ Preguntas abiertas</span>
          <span className="value" style={{ color: open_questions.length > 0 ? "var(--accent)" : "inherit" }}>
            {open_questions.length}
          </span>
        </div>
        <div className="tile">
          <span className="label">👥 Pool disponible</span>
          <span className="value">{pool.length}</span>
        </div>
      </div>

      <section className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
          <h3 style={{ margin: 0 }}>👤 Mis Requisitos Asignados ({assigned.length})</h3>
          <span className="muted" style={{ fontSize: "12px" }}>Pelota actualmente en tus manos</span>
        </div>
        {assigned.length === 0 ? (
          <p className="empty">No tienes requisitos asignados actualmente. Puedes tomar uno de la bolsa general abajo.</p>
        ) : (
          <div style={{ display: "grid", gap: "10px" }}>
            {assigned.map((r: Requirement) => {
              const pBadge = r.priority && r.priority in PRIORITY_STYLE ? PRIORITY_STYLE[r.priority] : null;
              return (
                <div
                  key={r.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "12px 16px",
                    background: "var(--surface-2)",
                    borderRadius: "8px",
                    border: "1px solid var(--line)",
                    flexWrap: "wrap",
                    gap: "10px",
                  }}
                >
                  <div style={{ display: "grid", gap: "4px" }}>
                    <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                      <span className="num" style={{ fontWeight: 700 }}>#{r.id}</span>
                      <strong style={{ fontSize: "15px" }}>{r.title}</strong>
                      <span className="chip">{r.project}</span>
                      {r.capability && <span className="chip">{r.capability}</span>}
                      {r.ricefw && <span className="chip">RICEFW {r.ricefw}</span>}
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
                      {r.due_date && (
                        <span className="chip" title={`SLA: ${new Date(r.due_date).toLocaleDateString()}`}>
                          📅 {new Date(r.due_date).toLocaleDateString()}
                        </span>
                      )}
                      <StateBadge state={r.state} />
                    </div>
                    <span className="muted" style={{ fontSize: "12px" }}>
                      Etapa actual: <strong>{r.stage}</strong> · Inversión IA: ${r.spent_usd.toFixed(4)}
                    </span>
                  </div>
                  <div className="gate-buttons">
                    <button
                      className="outline"
                      style={{ padding: "4px 10px", fontSize: "12px" }}
                      onClick={() => {
                        const target = window.prompt("Pasar pelota al usuario:");
                        if (target) perform(api.transfer(session, r.id, target), `Transferido a ${target}`);
                      }}
                    >
                      Pasar pelota
                    </button>
                    <button
                      className="ghost"
                      style={{ padding: "4px 10px", fontSize: "12px" }}
                      onClick={() => perform(api.release(session, r.id), "Devuelto al pool")}
                    >
                      Devolver
                    </button>
                    <button style={{ padding: "4px 12px", fontSize: "12px" }} onClick={() => onOpen(r.id)}>
                      Abrir →
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {waiting_gates.length > 0 && (
        <section className="card" style={{ borderLeft: "4px solid var(--warning)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
            <h3 style={{ margin: 0 }}>🚦 Compuertas Pendientes de Decisión ({waiting_gates.length})</h3>
            <span className="chip" style={{ background: "rgba(234, 179, 8, 0.15)", color: "var(--warning)" }}>
              Requiere aprobación
            </span>
          </div>
          <div style={{ display: "grid", gap: "10px" }}>
            {waiting_gates.map((r: Requirement) => {
              const pBadge = r.priority && r.priority in PRIORITY_STYLE ? PRIORITY_STYLE[r.priority] : null;
              return (
                <div
                  key={r.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "10px 14px",
                    background: "var(--surface-2)",
                    borderRadius: "8px",
                    border: "1px solid var(--line)",
                    flexWrap: "wrap",
                    gap: "8px",
                  }}
                >
                  <div>
                    <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                      <span className="num">#{r.id}</span>
                      <strong>{r.title}</strong>
                      <span className="chip">{r.project}</span>
                      <span className="chip" style={{ fontWeight: 600 }}>Etapa: {r.stage}</span>
                      {pBadge && (
                        <span
                          className="chip"
                          style={{
                            background: pBadge.bg,
                            color: pBadge.color,
                          }}
                        >
                          {pBadge.label}
                        </span>
                      )}
                    </div>
                  </div>
                  <button
                    style={{ padding: "5px 12px", fontSize: "12px", background: "var(--warning)", color: "#000" }}
                    onClick={() => onOpen(r.id)}
                  >
                    ⚡ Revisar y Decidir
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {open_questions.length > 0 && (
        <section className="card" style={{ borderLeft: "4px solid var(--accent)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
            <h3 style={{ margin: 0 }}>❓ Preguntas de la Fábrica por Resolver ({open_questions.length})</h3>
            <span className="chip" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
              Interacción Funcional
            </span>
          </div>
          <div style={{ display: "grid", gap: "12px" }}>
            {open_questions.map((q) => (
              <div
                key={q.id}
                style={{
                  padding: "12px",
                  background: "var(--surface-2)",
                  borderRadius: "8px",
                  border: "1px solid var(--line)",
                  display: "grid",
                  gap: "8px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                    <span className="chip">Requisito #{q.requirement_id}</span>
                    <span className="chip">De: <strong>{q.sender}</strong></span>
                    <span className="muted" style={{ fontSize: "12px" }}>
                      {new Date(q.created_at).toLocaleString()}
                    </span>
                  </div>
                  <button
                    className="ghost"
                    style={{ fontSize: "12px", padding: "2px 8px" }}
                    onClick={() => onOpen(q.requirement_id)}
                  >
                    Ver detalle →
                  </button>
                </div>
                <p style={{ margin: 0, fontSize: "14px", lineHeight: 1.4 }}>{q.body}</p>
                <div style={{ display: "flex", gap: "8px", marginTop: "4px" }}>
                  <input
                    style={{ flex: 1, padding: "6px 10px", fontSize: "13px" }}
                    placeholder="Escribe tu aclaración funcional para que la fábrica continúe…"
                    value={answers[q.id] || ""}
                    onChange={(e) => setAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        submitAnswer(q.requirement_id, q.id);
                      }
                    }}
                  />
                  <button
                    style={{ padding: "6px 14px", fontSize: "13px" }}
                    disabled={submitting[q.id] || !(answers[q.id] || "").trim()}
                    onClick={() => submitAnswer(q.requirement_id, q.id)}
                  >
                    {submitting[q.id] ? "Enviando…" : "Enviar respuesta"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
          <h3 style={{ margin: 0 }}>👥 Bolsa de Requisitos Disponibles (Pool) ({pool.length})</h3>
          <span className="muted" style={{ fontSize: "12px" }}>Cualquier consultor puede auto-asignarse una tarea</span>
        </div>
        {pool.length === 0 ? (
          <p className="empty">No hay requisitos libres en el pool actualmente.</p>
        ) : (
          <div style={{ display: "grid", gap: "10px" }}>
            {pool.map((r: Requirement) => {
              const pBadge = r.priority && r.priority in PRIORITY_STYLE ? PRIORITY_STYLE[r.priority] : null;
              return (
                <div
                  key={r.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "10px 14px",
                    background: "var(--surface-2)",
                    borderRadius: "8px",
                    border: "1px solid var(--line)",
                    flexWrap: "wrap",
                    gap: "8px",
                  }}
                >
                  <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                    <span className="num">#{r.id}</span>
                    <strong>{r.title}</strong>
                    <span className="chip">{r.project}</span>
                    {r.capability && <span className="chip">{r.capability}</span>}
                    {r.ricefw && <span className="chip">RICEFW {r.ricefw}</span>}
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
                    {r.due_date && (
                      <span className="chip">📅 {new Date(r.due_date).toLocaleDateString()}</span>
                    )}
                    <span className="chip">Etapa: {r.stage}</span>
                  </div>
                  <div className="gate-buttons">
                    <button
                      style={{ padding: "4px 10px", fontSize: "12px" }}
                      onClick={() => perform(api.claim(session, r.id), `Has tomado el requisito #${r.id}`)}
                    >
                      ✋ Reclamar tarea
                    </button>
                    <button
                      className="outline"
                      style={{ padding: "4px 10px", fontSize: "12px" }}
                      onClick={() => onOpen(r.id)}
                    >
                      Ver
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </section>
  );
}
