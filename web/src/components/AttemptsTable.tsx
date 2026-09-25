import type { Attempt } from "../types";

const usd = (n: number) => `$${n.toFixed(4)}`;

export function AttemptsTable({ attempts }: { attempts: Attempt[] }) {
  if (attempts.length === 0) return <p className="muted">Sin intentos todavía.</p>;
  const total = attempts.reduce((sum, a) => sum + a.cost_usd, 0);
  return (
    <table className="attempts">
      <thead>
        <tr>
          <th>Actividad</th>
          <th>Nivel</th>
          <th>Modelo</th>
          <th>Tokens</th>
          <th>Costo</th>
          <th>Resultado</th>
        </tr>
      </thead>
      <tbody>
        {attempts.map((a) => (
          <tr key={a.id} className={a.passed ? "ok" : "fail"}>
            <td>{a.activity}</td>
            <td>
              <span className={`tier tier-${a.tier}`}>{a.tier}</span>
            </td>
            <td>
              {a.provider} · {a.model}
            </td>
            <td>
              {a.tokens_in} / {a.tokens_out}
            </td>
            <td>{usd(a.cost_usd)}</td>
            <td title={a.issues.join("\n")}>
              {a.passed ? "✅ pasó" : `❌ ${a.issues.length} problema(s)`}
            </td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <td colSpan={4}>Total</td>
          <td>{usd(total)}</td>
          <td />
        </tr>
      </tfoot>
    </table>
  );
}
