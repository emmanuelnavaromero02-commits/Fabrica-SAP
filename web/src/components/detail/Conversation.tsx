import { useState } from "react";

import type { Message, MessageKind } from "../../types";

const KIND_LABEL: Record<MessageKind, string> = {
  pregunta: "Pregunta",
  respuesta: "Respuesta",
  propuesta: "Propuesta",
  objecion: "Objeción",
  evidencia: "Evidencia",
  escalamiento: "Escalamiento",
  decision: "Decisión",
  info: "Información",
};

const AGENTS: Record<string, string> = {
  clasificador: "🏷️",
  analista: "🔎",
  arquitecto: "📐",
  estimador: "⏱️",
  desarrollador: "🧑‍💻",
  revisor: "🧐",
  documentador: "📝",
  verificador: "✅",
  router: "🧭",
  motor: "⚙️",
};

function avatar(sender: string): string {
  const agent = sender.split("@")[0] ?? sender;
  return AGENTS[agent] ?? "👤";
}

interface Props {
  messages: Message[];
  onAnswer: (messageId: number, body: string) => Promise<void>;
}

export function Conversation({ messages, onAnswer }: Props) {
  const [filter, setFilter] = useState<"todo" | "personas" | "problemas">("todo");
  const shown = messages.filter((m) =>
    filter === "todo"
      ? true
      : filter === "problemas"
        ? ["objecion", "escalamiento", "pregunta"].includes(m.kind)
        : avatar(m.sender) === "👤",
  );
  return (
    <div>
      <div className="gate-buttons" role="group" aria-label="Filtro de conversación">
        {(["todo", "personas", "problemas"] as const).map((f) => (
          <button key={f} className={filter === f ? "outline" : "ghost"} onClick={() => setFilter(f)}>
            {f === "todo" ? "Todo" : f === "personas" ? "Solo personas" : "Preguntas y problemas"}
          </button>
        ))}
      </div>
      <ul className="chat">
        {shown.map((m) => (
          <li key={m.id} className={`bubble kind-${m.kind}`}>
            <span className="avatar" aria-hidden>
              {avatar(m.sender)}
            </span>
            <div className="bubble-body">
              <div className="bubble-head">
                <strong>{m.sender}</strong>
                {m.recipient && <span className="muted">→ {m.recipient}</span>}
                <span className="chip">{KIND_LABEL[m.kind]}</span>
                <span className="chip">#{m.thread}</span>
                <time>{new Date(m.created_at).toLocaleTimeString()}</time>
              </div>
              <p>{m.body}</p>
              {Array.isArray(m.data.issues) && m.data.issues.length > 0 && (
                <ul className="issues">
                  {(m.data.issues as string[]).map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              )}
              {m.kind === "pregunta" && !m.resolved && <AnswerBox id={m.id} onAnswer={onAnswer} />}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

const QUICK_SUGGESTIONS = [
  "Sí, incluir",
  "No, excluir",
  "Aplica a todas las sociedades",
  "Solo empleados activos",
  "Usar estándar SAP",
  "Conforme con la propuesta",
];

function AnswerBox({ id, onAnswer }: { id: number; onAnswer: Props["onAnswer"] }) {
  const [text, setText] = useState("");
  return (
    <div style={{ marginTop: "8px" }}>
      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "6px" }}>
        {QUICK_SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            className="ghost"
            style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "12px", background: "var(--bg-card, #f0f4f8)" }}
            onClick={() => setText((prev) => (prev ? `${prev} · ${s}` : s))}
          >
            + {s}
          </button>
        ))}
      </div>
      <form
        className="answer"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) void onAnswer(id, text).then(() => setText(""));
        }}
      >
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Escribe la respuesta para la fábrica…" />
        <button type="submit">Responder</button>
      </form>
    </div>
  );
}
