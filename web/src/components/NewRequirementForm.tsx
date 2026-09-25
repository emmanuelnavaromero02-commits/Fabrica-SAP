import { type FormEvent, useState } from "react";

import type { NewRequirement } from "../types";

interface Props {
  onCreate: (data: NewRequirement, files: File[]) => Promise<void>;
}

export function NewRequirementForm({ onCreate }: Props) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [spec, setSpec] = useState("");
  const [files, setFiles] = useState<File[]>([]);
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
      }, files);
      setTitle("");
      setDescription("");
      setSpec("");
      setFiles([]);
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
      <label className="files">
        📎 Documentos (PDF, DOCX, TXT)
        <input
          type="file"
          multiple
          accept=".pdf,.docx,.txt,.md,.vtt,.srt,.csv"
          onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
        />
      </label>
      {files.length > 0 && <p className="muted">{files.map((f) => f.name).join(", ")}</p>}
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy}>
        {busy ? "Creando…" : "Crear y enviar a la fábrica"}
      </button>
    </form>
  );
}
