import { useState } from "react";

import { api } from "../api";
import type { Session } from "../auth";
import { usePolling } from "../hooks";
import type { Outcome, Stage } from "../types";
import { Conversation } from "./detail/Conversation";
import { Deliverables } from "./detail/Deliverables";
import { EscalationLadder } from "./detail/EscalationLadder";
import { SapPanel } from "./detail/SapPanel";
import { Stepper } from "./detail/Stepper";
import { Summary } from "./detail/Summary";
import { DocumentsPanel } from "./DocumentsPanel";
import { GateActions } from "./GateActions";
import { StateBadge } from "./StateBadge";
import { errorText, useToast } from "./ui/Toasts";

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
