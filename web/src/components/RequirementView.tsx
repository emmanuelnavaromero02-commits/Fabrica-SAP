import { useState } from "react";

import { api } from "../api";
import type { Session } from "../auth";
import { usePolling } from "../hooks";
import type { Outcome, Stage } from "../types";
import { AttemptsTable } from "./AttemptsTable";
import { BoardFeed } from "./BoardFeed";
import { GateActions } from "./GateActions";
import { StageTimeline } from "./StageTimeline";
import { StateBadge } from "./StateBadge";

type Tab = "tablero" | "escalamiento" | "git" | "decisiones";

const TABS: { key: Tab; label: string }[] = [
  { key: "tablero", label: "🗨️ Tablero" },
  { key: "escalamiento", label: "⬆️ Escalamiento" },
  { key: "git", label: "📦 Git" },
  { key: "decisiones", label: "⚖️ Decisiones" },
];

interface Props {
  session: Session;
  id: number;
  stages: Stage[];
  onChanged: () => void;
}

export function RequirementView({ session, id, stages, onChanged }: Props) {
  const [tab, setTab] = useState<Tab>("tablero");
  const { data, error, refresh } = usePolling(() => api.detail(session, id), 2000);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Cargando…</p>;

  const req = data.requirement;
  const stage = stages.find((s) => s.key === req.stage);
  const after = async (action: Promise<void>) => {
    await action;
    await refresh();
    onChanged();
  };

  return (
    <section className="detail">
      <header>
        <h2>
          #{req.id} {req.title}
        </h2>
        <StateBadge state={req.state} />
      </header>
      <p className="muted">
        {req.capability ?? "—"} · RICEFW {req.ricefw ?? "—"} · creado por {req.created_by} · gasto
        IA ${req.spent_usd.toFixed(4)}
      </p>
      <p>{req.description}</p>

      <StageTimeline stages={stages} current={req.stage} />
      <GateActions
        session={session}
        requirement={req}
        stage={stage}
        onDecide={(outcome: Outcome, comment: string) =>
          after(api.decide(session, id, outcome, comment))
        }
        onResume={() => after(api.resume(session, id))}
      />

      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={t.key === tab ? "tab active" : "tab"}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "tablero" && (
        <BoardFeed
          messages={data.messages}
          onAnswer={(messageId, body) => after(api.answer(session, id, messageId, body))}
        />
      )}
      {tab === "escalamiento" && <AttemptsTable attempts={data.attempts} />}
      {tab === "git" && (
        <div>
          <p className="muted">Repositorio: {req.repo_url ?? "aún no creado"}</p>
          <ul className="artifacts">
            {data.artifacts.map((a) => (
              <li key={a.id}>
                <code>{a.path}</code> · {a.author} · <code>{a.commit.slice(0, 8)}</code>
              </li>
            ))}
          </ul>
        </div>
      )}
      {tab === "decisiones" && (
        <ul className="artifacts">
          {data.decisions.length === 0 && <li className="muted">Sin decisiones todavía.</li>}
          {data.decisions.map((d) => (
            <li key={d.id}>
              <strong>{d.outcome}</strong> en {d.stage} por {d.actor} ({d.role})
              {d.comment && ` — ${d.comment}`}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
