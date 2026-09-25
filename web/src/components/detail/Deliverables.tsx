import { useEffect, useMemo, useState } from "react";

import { api } from "../../api";
import type { Session } from "../../auth";
import type { Artifact } from "../../types";
import { errorText } from "../ui/Toasts";
import { CodeViewer } from "./CodeViewer";

const PRIORITY = ["diseno/spec.md", "src/", "diseno/estimacion.json", "docs/", "evidencia/", "inputs/"];

function rank(path: string): number {
  const index = PRIORITY.findIndex((p) => path.startsWith(p));
  return index === -1 ? PRIORITY.length : index;
}

interface Props {
  session: Session;
  id: number;
  artifacts: Artifact[];
  repoUrl: string | null;
}

export function Deliverables({ session, id, artifacts, repoUrl }: Props) {
  const paths = useMemo(
    () => [...new Set(artifacts.map((a) => a.path))].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b)),
    [artifacts],
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const current = selected ?? paths[0] ?? null;

  useEffect(() => {
    if (!current) return;
    setError(null);
    api.file(session, id, current).then(
      (file) => setContent(file.content),
      (e: unknown) => setError(errorText(e)),
    );
  }, [current, id, session.user, session.role]);

  if (!paths.length) return <p className="empty">Todavía no hay entregables en el repositorio.</p>;
  const last = artifacts.filter((a) => a.path === current).at(-1);

  return (
    <div className="files">
      <ul className="file-list" aria-label="Archivos del repositorio">
        {paths.map((p) => (
          <li key={p}>
            <button className={p === current ? "active" : ""} onClick={() => setSelected(p)}>
              {p}
            </button>
          </li>
        ))}
        {repoUrl && <li className="repo-url">{repoUrl}</li>}
      </ul>
      <div className="viewer">
        {last && (
          <p className="viewer-meta">
            {last.author} · commit <code>{last.commit.slice(0, 8)}</code>
          </p>
        )}
        {error ? <p className="error viewer-meta">{error}</p> : current && <CodeViewer path={current} source={content} />}
      </div>
    </div>
  );
}
