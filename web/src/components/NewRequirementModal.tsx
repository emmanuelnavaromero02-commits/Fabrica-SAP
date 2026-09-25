import { type DragEvent, type FormEvent, useRef, useState } from "react";

import type { NewRequirement } from "../types";
import { errorText } from "../errors";

interface Props {
  onClose: () => void;
  onCreate: (data: NewRequirement, files: File[]) => Promise<void>;
}

export function NewRequirementModal({ onClose, onCreate }: Props) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [project, setProject] = useState("demo");
  const [ricefw, setRicefw] = useState("Reporte");
  const [capability, setCapability] = useState("FI");
  const [spec, setSpec] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const pressedOnBackdrop = useRef(false);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addFiles = (list: FileList | null) => setFiles((f) => [...f, ...Array.from(list ?? [])]);

  function drop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    addFiles(e.dataTransfer.files);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const documents = spec.trim()
        ? [{ name: "especificacion", kind: "especificacion", content: spec.trim() }]
        : [];
      await onCreate({ title, description, project, capability, ricefw, documents }, files);
      onClose();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Nuevo requisito"
      onMouseDown={(e) => {
        pressedOnBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        if (pressedOnBackdrop.current && e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <form className="modal" onSubmit={submit}>
        <h2>Nuevo requisito</h2>
        <label>
          Título
          <input value={title} onChange={(e) => setTitle(e.target.value)} minLength={3} required autoFocus />
        </label>
        <label>
          Descripción
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} minLength={10} required rows={4} />
        </label>
        <label>
          Especificación funcional (opcional, también puedes adjuntar archivos)
          <textarea value={spec} onChange={(e) => setSpec(e.target.value)} rows={4} />
        </label>
        <div style={{ display: "flex", gap: "10px" }}>
          <label style={{ flex: 1 }}>
            Proyecto
            <input value={project} onChange={(e) => setProject(e.target.value)} required />
          </label>
          <label style={{ flex: 1 }}>
            Tipo RICEFW
            <select value={ricefw} onChange={(e) => setRicefw(e.target.value)}>
              <option value="Reporte">Reporte (R)</option>
              <option value="Interfaz">Interfaz (I)</option>
              <option value="Conversion">Conversión (C)</option>
              <option value="Mejora">Mejora / Enhancement (E)</option>
              <option value="Formulario">Formulario (F)</option>
              <option value="Workflow">Workflow (W)</option>
            </select>
          </label>
          <label style={{ width: "100px" }}>
            Módulo
            <input
              value={capability}
              onChange={(e) => setCapability(e.target.value.toUpperCase())}
              placeholder="FI, MM, SD"
            />
          </label>
        </div>
        <div
          className={over ? "dropzone over" : "dropzone"}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={drop}
        >
          <strong>Arrastra aquí especificaciones, transcripciones o cuestionarios</strong>
          <span className="muted">PDF, DOCX, TXT, VTT o SRT</span>
          <input type="file" multiple accept=".pdf,.docx,.txt,.md,.vtt,.srt,.csv" onChange={(e) => addFiles(e.target.files)} />
        </div>
        {files.length > 0 && (
          <ul className="file-list">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className="chip">
                📎 {f.name}
                <button type="button" className="ghost" onClick={() => setFiles(files.filter((_, j) => j !== i))}>
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}
        {files.length === 0 && !spec.trim() && (
          <p className="muted">Sin documentos, el Analista preguntará al cliente lo que falte antes de diseñar.</p>
        )}
        {error && <p className="error">{error}</p>}
        <div className="modal-actions">
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button type="submit" disabled={busy}>
            {busy ? "Enviando…" : "🚀 Enviar a la fábrica"}
          </button>
        </div>
      </form>
    </div>
  );
}
