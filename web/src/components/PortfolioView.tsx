import { api } from "../api";
import type { Session } from "../auth";
import { usePolling } from "../hooks";

const pct = (value: number) => `${Math.round(value * 100)} %`;

export function PortfolioView({ session, onOpen }: { session: Session; onOpen: (id: number) => void }) {
  const { data, error } = usePolling(() => api.portfolio(session), 10_000);
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Cargando…</p>;

  return (
    <section className="report">
      <h2>Portafolio</h2>
      <table className="summary">
        <tbody>
          <tr><th>Requisitos</th><td>{data.total}</td></tr>
          <tr><th>Tiempo de ciclo promedio</th><td>{data.lead_time_days ?? "—"} días</td></tr>
          <tr><th>Costo de IA acumulado</th><td>${data.ai_cost_usd.toFixed(2)}</td></tr>
          {Object.entries(data.by_stage).map(([stage, count]) => (
            <tr key={stage}><th>En {stage}</th><td>{count}</td></tr>
          ))}
        </tbody>
      </table>

      <h3>Aprobado al primer intento, por actividad</h3>
      <table className="summary">
        <tbody>
          {Object.entries(data.first_pass_rate).map(([activity, rate]) => (
            <tr key={activity}><th>{activity}</th><td>{pct(rate)}</td></tr>
          ))}
        </tbody>
      </table>

      {data.blocked.length > 0 && (
        <>
          <h3>⛔ Bloqueados</h3>
          <ul>
            {data.blocked.map((r) => (
              <li key={r.id}><button className="link" onClick={() => onOpen(r.id)}>#{r.id} {r.title}</button> · {r.stage}</li>
            ))}
          </ul>
        </>
      )}

      <h3>Estimado vs. trabajado</h3>
      <table className="attempts">
        <thead>
          <tr><th>#</th><th>Requisito</th><th>Etapa</th><th>Estimado</th><th>Trabajado</th><th>Costo IA</th></tr>
        </thead>
        <tbody>
          {data.requirements.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td><button className="link" onClick={() => onOpen(r.id)}>{r.title}</button></td>
              <td>{r.stage}</td>
              <td>{r.estimated_hours ?? "—"} h</td>
              <td>{r.worked_hours} h</td>
              <td>${r.ai_cost_usd.toFixed(4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
