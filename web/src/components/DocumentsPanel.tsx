import { useState } from "react";

import { api } from "../api";
import type { Session } from "../auth";
import { errorText } from "../errors";
import { usePolling } from "../hooks";

export function DocumentsPanel({ session, id }: { session: Session; id: number }) {
  const { data, refresh } = usePolling(() => api.documents(session, id), 5000);
  const [error, setError] = useState<string | null>(null);

  async function upload(files: File[]) {
    if (!files.length) return;
    setError(null);
    try {
      await api.addDocuments(session, id, files);
      await refresh();
    } catch (e) {
      setError(errorText(e));
    }
  }

  return (
    <div className="card">
      <label className="dropzone">
        <strong>📎 Agregar documentos al requisito</strong>
        <span className="muted">Si el requisito estaba en pausa sin preguntas abiertas, la fábrica lo retoma</span>
        <input type="file" multiple onChange={(e) => void upload(Array.from(e.target.files ?? []))} />
      </label>
      {data && data.length === 0 && <p className="empty">Sin documentos de entrada.</p>}
      {error && <p className="error">{error}</p>}
      <ul className="docs">
        {(data ?? []).map((d) => (
          <li key={d.id}>
            <details>
              <summary>
                {d.name} <span className="muted">({d.kind}, {d.content.length} caracteres)</span>
              </summary>
              <pre>{d.content.slice(0, 4000)}</pre>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}
