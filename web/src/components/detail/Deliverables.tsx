import { useEffect, useMemo, useState } from "react";

import { api } from "../../api";
import type { Session } from "../../auth";
import type { Artifact } from "../../types";
import { errorText } from "../../errors";
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
  const last = artifacts.filter((a) => a.path === current).at(-1);
  const version = last?.commit ?? "";

  useEffect(() => {
    if (!current) return;
    let active = true;
    setError(null);
    setContent("");
    api.file(session, id, current).then(
      (file) => active && setContent(file.content),
      (e: unknown) => active && setError(errorText(e)),
    );
    return () => {
      active = false;
    };
  }, [current, version, id, session.user, session.role]);

  if (!paths.length) return <p className="empty">Todavía no hay entregables en el repositorio.</p>;

  const [viewMode, setViewMode] = useState<"code" | "preview">("preview");
  const isHtml = current?.endsWith(".html") ?? false;

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
        <div className="viewer-meta" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          {last && (
            <span>
              {last.author} · commit <code>{last.commit.slice(0, 8)}</code>
            </span>
          )}
          {isHtml && (
            <div className="gate-buttons">
              <button
                className={viewMode === "preview" ? "active" : ""}
                onClick={() => setViewMode("preview")}
                style={{ padding: "2px 8px", fontSize: "12px" }}
              >
                Vista previa
              </button>
              <button
                className={viewMode === "code" ? "active" : ""}
                onClick={() => setViewMode("code")}
                style={{ padding: "2px 8px", fontSize: "12px" }}
              >
                Código HTML
              </button>
            </div>
          )}
        </div>
        {error ? (
          <p className="error viewer-meta">{error}</p>
        ) : current && isHtml && viewMode === "preview" ? (
          <iframe
            sandbox="allow-scripts"
            srcDoc={content}
            style={{ width: "100%", height: "550px", border: "1px solid var(--line)", borderRadius: "6px", background: "#fff" }}
            title="Prototipo Fiori"
          />
        ) : (
          current && <CodeViewer path={current} source={content} />
        )}
      </div>
    </div>
  );
}
