import { api } from "../api";
import type { Session } from "../auth";
import { usePolling } from "../hooks";
import type { Stage } from "../types";
import { BarList } from "./ui/BarList";

const pct = (value: number) => `${Math.round(value * 100)} %`;

interface Props {
  session: Session;
  stages: Stage[];
  onOpen: (id: number) => void;
}

export function PortfolioView({ session, stages, onOpen }: Props) {
  const { data, error } = usePolling(() => api.portfolio(session), 10_000);
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Cargando…</p>;

  const label = (key: string) => stages.find((s) => s.key === key)?.label ?? key;
  const byStage = stages
    .filter((s) => data.by_stage[s.key])
    .map((s) => ({ key: s.key, label: s.label, value: data.by_stage[s.key] ?? 0 }));
  const firstPass = Object.entries(data.first_pass_rate).map(([activity, rate]) => ({
    key: activity,
    label: activity,
    value: rate,
  }));
  const tiles = [
    { label: "Requisitos", value: String(data.total) },
    { label: "Agentes trabajando", value: String(data.by_state.running ?? 0) },
    { label: "Esperan decisión", value: String(data.waiting.length) },
    { label: "En pausa", value: String(data.blocked.length) },
    { label: "Ciclo promedio", value: data.lead_time_days === null ? "—" : `${data.lead_time_days} d` },
    { label: "Costo de IA", value: `$${data.ai_cost_usd.toFixed(2)}` },
  ];

  return (
    <section className="page">
      <div className="tiles">
        {tiles.map((t) => (
          <div key={t.label} className="tile">
            <span className="label">{t.label}</span>
            <span className="value">{t.value}</span>
          </div>
        ))}
      </div>

      <div className="two">
        <BarList title="Requisitos por etapa" bars={byStage} />
        <BarList title="Aprobado al primer intento, por actividad" bars={firstPass} max={1} format={pct} />
      </div>

      {(data.blocked.length > 0 || data.waiting.length > 0) && (
        <div className="two">
          {[
            { title: "⛔ En pausa", rows: data.blocked },
            { title: "👤 Esperan una decisión", rows: data.waiting },
          ].map((group) => (
            <section key={group.title} className="card">
              <h3>{group.title}</h3>
              {group.rows.length === 0 && <p className="muted">Ninguno.</p>}
              {group.rows.map((r) => (
                <p key={r.id}>
                  <button className="link" onClick={() => onOpen(r.id)}>
                    #{r.id} {r.title}
                  </button>
                  <span className="muted"> · {label(r.stage)}</span>
                </p>
              ))}
            </section>
          ))}
        </div>
      )}

      <section className="card">
        <h3>Estimado contra trabajado</h3>
        <table className="grid">
          <thead>
            <tr>
              <th>#</th>
              <th>Requisito</th>
              <th>Etapa</th>
              <th>Estimado</th>
              <th>Trabajado</th>
              <th>Costo IA</th>
            </tr>
          </thead>
          <tbody>
            {data.requirements.map((r) => (
              <tr key={r.id}>
                <td className="num">{r.id}</td>
                <td>
                  <button className="link" onClick={() => onOpen(r.id)}>
                    {r.title}
                  </button>
                </td>
                <td>{label(r.stage)}</td>
                <td className="num">{r.estimated_hours === null ? "—" : `${r.estimated_hours} h`}</td>
                <td className="num">{r.worked_hours} h</td>
                <td className="num">${r.ai_cost_usd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </section>
  );
}
