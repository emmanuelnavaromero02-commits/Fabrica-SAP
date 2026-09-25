import type { Estimate } from "../types";

export function EstimateCard({ estimate }: { estimate: Estimate }) {
  return (
    <div className="estimate">
      <strong>
        ⏱ {estimate.hours_total} h · {estimate.days} días · complejidad {estimate.complexity}
      </strong>
      <ul>
        {estimate.items.map((item) => (
          <li key={item.object}>
            <code>{item.object}</code> talla {item.size} ({item.hours} h) — {item.rationale}
          </li>
        ))}
      </ul>
      {estimate.assumptions.length > 0 && (
        <p className="muted">Asunciones: {estimate.assumptions.join("; ")}</p>
      )}
    </div>
  );
}
