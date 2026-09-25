import { api } from "../api";
import type { Session } from "../auth";
import { usePolling } from "../hooks";
import { BarList } from "./ui/BarList";

export function TimeView({ session }: { session: Session }) {
  const { data, error } = usePolling(() => api.time(session), 30_000);
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Cargando…</p>;
  const total = data.reduce((sum, row) => sum + row.hours, 0);
  const perUser = new Map<string, number>();
  for (const row of data) perUser.set(row.user, (perUser.get(row.user) ?? 0) + row.hours);
  const bars = [...perUser].map(([user, hours]) => ({ key: user, label: user, value: hours }));

  return (
    <section className="page">
      <p className="muted">
        Tiempo activo en el portal y en Claude Code, Codex o VS Code a través de los MCP. Tras 5 minutos sin
        actividad deja de contar.
      </p>
      <BarList title="Horas por persona" bars={bars} format={(h) => `${h.toFixed(2)} h`} />
      <section className="card">
      <table className="grid">
        <thead>
          <tr><th>Persona</th><th>Requisito</th><th>Portal</th><th>MCP</th><th>Total</th></tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr key={`${row.user}-${row.requirement_id ?? "general"}`}>
              <td>{row.user}</td>
              <td>{row.requirement_id ? `#${row.requirement_id}` : "general"}</td>
              <td>{row.by_source.web ?? 0} h</td>
              <td>{row.by_source.mcp ?? 0} h</td>
              <td>{row.hours} h</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr><td colSpan={4}>Total</td><td>{total.toFixed(2)} h</td></tr>
        </tfoot>
      </table>
      </section>
    </section>
  );
}
