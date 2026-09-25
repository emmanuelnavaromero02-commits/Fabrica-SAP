import type { RequirementDetail } from "../../types";

export function Summary({ data }: { data: RequirementDetail }) {
  const { requirement: req, estimate, decisions, transports } = data;
  return (
    <div className="two">
      <section className="card">
        <h3>Descripción</h3>
        <p>{req.description}</p>
        <div className="facts">
          <span>Proyecto <strong>{req.project}</strong></span>
          <span>Módulo <strong>{req.capability ?? "—"}</strong></span>
          <span>RICEFW <strong>{req.ricefw ?? "—"}</strong></span>
          <span>Creado por <strong>{req.created_by}</strong></span>
          <span>Alta <strong>{new Date(req.created_at).toLocaleDateString()}</strong></span>
        </div>
      </section>
      <section className="card">
        <h3>⏱ Estimación</h3>
        {estimate ? (
          <>
            <div className="tiles">
              <div className="tile"><span className="label">Horas</span><span className="value">{estimate.hours_total}</span></div>
              <div className="tile"><span className="label">Días</span><span className="value">{estimate.days}</span></div>
              <div className="tile"><span className="label">Complejidad</span><span className="value">{estimate.complexity}</span></div>
            </div>
            <table className="grid">
              <tbody>
                {estimate.items.map((item) => (
                  <tr key={item.object}>
                    <td><code>{item.object}</code></td>
                    <td><span className="chip">{item.size}</span></td>
                    <td className="num">{item.hours} h</td>
                    <td>{item.rationale}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {estimate.assumptions.length > 0 && <p className="muted">Asunciones: {estimate.assumptions.join("; ")}</p>}
          </>
        ) : (
          <p className="muted">Se calcula al terminar el diseño.</p>
        )}
      </section>
      <section className="card">
        <h3>⚖️ Decisiones</h3>
        {decisions.length === 0 && <p className="muted">Sin decisiones todavía.</p>}
        <ul className="chat">
          {decisions.map((d) => (
            <li key={d.id}>
              <strong>{d.outcome === "approve" ? "✅ Aprobado" : d.outcome === "reject" ? "↩️ Devuelto" : "🚫 Desestimado"}</strong>{" "}
              en {d.stage} por {d.actor} ({d.role}) · <span className="muted">{new Date(d.created_at).toLocaleString()}</span>
              {d.comment && <p>{d.comment}</p>}
            </li>
          ))}
        </ul>
      </section>
      <section className="card">
        <h3>🚚 Transporte</h3>
        {transports.length === 0 ? (
          <p className="muted">Se crea cuando el desarrollador escribe en SAP DEV.</p>
        ) : (
          transports.map((t) => (
            <p key={t.id}>
              <code>{t.number}</code> · {t.system} · {t.objects.join(", ")}
            </p>
          ))
        )}
      </section>
    </div>
  );
}
