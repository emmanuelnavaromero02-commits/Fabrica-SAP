import type { SapCall, Transport } from "../../types";

export function SapPanel({ transports, calls }: { transports: Transport[]; calls: SapCall[] }) {
  return (
    <div className="two">
      <section className="card">
        <h3>🚚 Órdenes de transporte</h3>
        {transports.length === 0 && <p className="muted">Sin órdenes todavía.</p>}
        {transports.map((t) => (
          <div key={t.id}>
            <p>
              <code>{t.number}</code> en <strong>{t.system}</strong> · {t.status}
            </p>
            <div className="gate-buttons">
              {t.objects.map((o) => (
                <span key={o} className="chip">{o}</span>
              ))}
            </div>
          </div>
        ))}
        <p className="muted">La liberación de órdenes la hace siempre una persona de BASIS.</p>
      </section>
      <section className="card">
        <h3>🛡️ Auditoría del Puente SAP</h3>
        {calls.length === 0 ? (
          <p className="muted">Sin llamadas a SAP.</p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>Hora</th>
                <th>Operación</th>
                <th>Objeto</th>
                <th>Quién</th>
                <th>Resultado</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.id} title={c.detail}>
                  <td className="num">{new Date(c.created_at).toLocaleTimeString()}</td>
                  <td>{c.tool}</td>
                  <td><code>{c.object_name}</code></td>
                  <td>{c.actor}</td>
                  <td>{c.ok ? "✓ ok" : "✗ rechazado"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
