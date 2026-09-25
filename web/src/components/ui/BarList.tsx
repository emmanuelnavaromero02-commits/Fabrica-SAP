import { useState } from "react";

export interface Bar {
  key: string;
  label: string;
  value: number;
}

interface Props {
  title: string;
  bars: Bar[];
  max?: number;
  format?: (value: number) => string;
}

export function BarList({ title, bars, max, format = (v) => String(v) }: Props) {
  const [asTable, setAsTable] = useState(false);
  const top = max ?? Math.max(1, ...bars.map((b) => b.value));
  return (
    <section className="card">
      <div className="headline">
        <h3>{title}</h3>
        <span className="grow" />
        <button className="ghost" onClick={() => setAsTable(!asTable)}>
          {asTable ? "Ver gráfico" : "Ver tabla"}
        </button>
      </div>
      {bars.length === 0 && <p className="empty">Sin datos todavía.</p>}
      {asTable ? (
        <table className="grid">
          <tbody>
            {bars.map((b) => (
              <tr key={b.key}>
                <td>{b.label}</td>
                <td className="num">{format(b.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="bars" role="list">
          {bars.map((b) => (
            <div key={b.key} className="bar-row" role="listitem" aria-label={`${b.label}: ${format(b.value)}`}>
              <span>{b.label}</span>
              <span className="bar-track">
                <span className="bar-fill" style={{ width: `${(b.value / top) * 100}%`, display: "block" }} />
              </span>
              <span className="num">{format(b.value)}</span>
              <span className="bar-tip">
                {b.label}: {format(b.value)}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
