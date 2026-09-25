import { useState } from "react";

import type { Message, MessageKind } from "../types";

const KIND_ICON: Record<MessageKind, string> = {
  pregunta: "❓",
  respuesta: "💬",
  propuesta: "✅",
  objecion: "⚠️",
  evidencia: "📎",
  escalamiento: "⬆️",
  decision: "⚖️",
  info: "ℹ️",
};

interface Props {
  messages: Message[];
  onAnswer: (messageId: number, body: string) => Promise<void>;
}

/** El tablero: la conversación tipada entre agentes y personas. */
export function BoardFeed({ messages, onAnswer }: Props) {
  return (
    <ul className="feed">
      {messages.map((m) => (
        <li key={m.id} className={`msg msg-${m.kind}`}>
          <div className="msg-head">
            <span>{KIND_ICON[m.kind]}</span>
            <strong>{m.sender}</strong>
            {m.recipient && <span className="muted">→ {m.recipient}</span>}
            <span className="muted thread">#{m.thread}</span>
            <time className="muted">{new Date(m.created_at).toLocaleTimeString()}</time>
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
        </li>
      ))}
    </ul>
  );
}

function AnswerBox({ id, onAnswer }: { id: number; onAnswer: Props["onAnswer"] }) {
  const [text, setText] = useState("");
  return (
    <form
      className="answer"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) void onAnswer(id, text).then(() => setText(""));
      }}
    >
      <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Responder…" />
      <button type="submit">Responder</button>
    </form>
  );
}
