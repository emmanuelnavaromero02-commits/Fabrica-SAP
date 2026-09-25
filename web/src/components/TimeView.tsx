import { api } from "../api";
import type { Session } from "../auth";
import { usePolling } from "../hooks";

export function TimeView({ session }: { session: Session }) {
  const { data, error } = usePolling(() => api.time(session), 30_000);
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Cargando…</p>;
  const total = data.reduce((sum, row) => sum + row.hours, 0);

  return (
    <section className="report">
      <h2>Tiempo trabajado</h2>
      <p className="muted">Tiempo activo en el portal y en las herramientas conectadas por MCP.</p>
      <table className="attempts">
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
  );
}
