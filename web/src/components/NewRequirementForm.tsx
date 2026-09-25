import { type FormEvent, useState } from "react";

import type { NewRequirement } from "../types";

interface Props {
  onCreate: (data: NewRequirement) => Promise<void>;
}

export function NewRequirementForm({ onCreate }: Props) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [spec, setSpec] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onCreate({
        title,
        description,
        project: "demo",
        documents: spec.trim() ? [{ name: "especificacion", kind: "especificacion", content: spec }] : [],
      });
      setTitle("");
      setDescription("");
      setSpec("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="new-req" onSubmit={submit}>
      <h3>🚀 Nuevo requisito</h3>
      <input
        placeholder="Título (p. ej. Reporte de facturas contabilizadas)"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        minLength={3}
        required
      />
      <textarea
        placeholder="Descripción del requerimiento"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        minLength={10}
        required
        rows={3}
      />
      <textarea
        placeholder="Especificación funcional (opcional). Sin ella, el Analista preguntará al cliente."
        value={spec}
        onChange={(e) => setSpec(e.target.value)}
        rows={3}
      />
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy}>
        {busy ? "Creando…" : "Crear y enviar a la fábrica"}
      </button>
    </form>
  );
}
