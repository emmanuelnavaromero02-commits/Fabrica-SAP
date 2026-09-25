import type { Attempt } from "../../types";

const usd = (n: number) => `$${n.toFixed(4)}`;

export function EscalationLadder({ attempts }: { attempts: Attempt[] }) {
  if (!attempts.length) return <p className="empty">Sin intentos todavía.</p>;
  const activities = [...new Set(attempts.map((a) => a.activity))];
  const total = attempts.reduce((sum, a) => sum + a.cost_usd, 0);
  return (
    <div className="ladder">
      <p className="muted">
        Cada actividad empieza en su nivel más barato viable y sube solo si la verificación automática rechaza el
        resultado. Costo total de IA: <strong className="num">{usd(total)}</strong>
      </p>
      {activities.map((activity) => {
        const rows = attempts.filter((a) => a.activity === activity);
        const cost = rows.reduce((sum, a) => sum + a.cost_usd, 0);
        return (
          <div key={activity} className="ladder-row">
            <strong>{activity}</strong>
            <div className="ladder-steps">
              {rows.map((a, i) => (
                <span key={a.id} className={a.passed ? "attempt ok" : "attempt fail"} title={a.issues.join("\n") || "Pasó la verificación"}>
                  {i > 0 && <span aria-hidden>→</span>}
                  <span className={`tier tier-${a.tier}`}>{a.tier}</span>
                  {a.model}
                  <span aria-label={a.passed ? "pasó" : "falló"}>{a.passed ? "✓" : "✗"}</span>
                </span>
              ))}
            </div>
            <span className="num muted">{usd(cost)}</span>
          </div>
        );
      })}
      <details>
        <summary>Detalle de intentos</summary>
        <table className="grid">
          <thead>
            <tr>
              <th>Actividad</th>
              <th>Nivel</th>
              <th>Proveedor · modelo</th>
              <th>Tokens</th>
              <th>Costo</th>
              <th>Problemas</th>
            </tr>
          </thead>
          <tbody>
            {attempts.map((a) => (
              <tr key={a.id}>
                <td>{a.activity}</td>
                <td><span className={`tier tier-${a.tier}`}>{a.tier}</span></td>
                <td>{a.provider} · {a.model}</td>
                <td className="num">{a.tokens_in} / {a.tokens_out}</td>
                <td className="num">{usd(a.cost_usd)}</td>
                <td>{a.issues.length ? a.issues.join("; ") : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
