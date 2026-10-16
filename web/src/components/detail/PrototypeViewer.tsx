import { useEffect, useState } from "react";
import { api } from "../../api";
import type { Session } from "../../auth";
import { errorText } from "../../errors";

interface Props {
  session: Session;
  id: number;
}

export function PrototypeViewer({ session, id }: Props) {
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api.file(session, id, "diseno/prototipo.html").then(
      (file) => {
        if (!active) return;
        setContent(file.content);
        setLoading(false);
      },
      (e: unknown) => {
        if (!active) return;
        setError(errorText(e));
        setLoading(false);
      }
    );
    return () => {
      active = false;
    };
  }, [id, session.user, session.role]);

  if (loading) return <p className="muted" style={{ padding: "20px" }}>Cargando prototipo interactivo…</p>;
  if (error) {
    return (
      <div className="card" style={{ padding: "20px", textAlign: "center" }}>
        <p className="muted">El prototipo interactivo todavía no está disponible en este requerimiento.</p>
        <p className="muted" style={{ fontSize: "12px" }}>Se generará automáticamente al completar la etapa de diseño.</p>
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: "16px", marginTop: "12px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
        <div>
          <h3 style={{ margin: "0 0 4px 0" }}>🎨 Prototipo Interactivo SAP (ALV / Fiori)</h3>
          <p className="muted" style={{ margin: 0, fontSize: "13px" }}>
            Simulación interactiva para validación del usuario clave antes de construir en SAP.
          </p>
        </div>
        <button
          className="outline"
          onClick={() => {
            const blob = new Blob([content], { type: "text/html" });
            const url = URL.createObjectURL(blob);
            window.open(url, "_blank");
          }}
          style={{ fontSize: "12px", padding: "4px 10px" }}
        >
          ↗ Abrir en ventana completa
        </button>
      </div>
      <iframe
        sandbox="allow-scripts allow-forms allow-modals"
        srcDoc={content}
        style={{
          width: "100%",
          height: "650px",
          border: "1px solid var(--line)",
          borderRadius: "8px",
          background: "#ffffff",
        }}
        title="Prototipo Interactivo SAP"
      />
    </div>
  );
}
