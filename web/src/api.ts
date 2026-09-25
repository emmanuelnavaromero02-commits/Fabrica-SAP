import type { Session } from "./auth";
import type {
  DocumentFile,
  Identity,
  NewRequirement,
  Outcome,
  Portfolio,
  RepoFile,
  Requirement,
  RequirementDetail,
  Stage,
  TimeRow,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function request<T>(session: Session, path: string, init: RequestInit = {}): Promise<T> {
  const jsonBody = typeof init.body === "string";
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(jsonBody ? { "Content-Type": "application/json" } : {}),
      ...session.headers,
      ...init.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    const detail = typeof body.detail === "string" ? body.detail : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

function formWith(files: File[], fields: Record<string, string>): FormData {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) form.append(key, value);
  for (const file of files) {
    form.append("files", file);
    form.append("kinds", kindOf(file.name));
  }
  return form;
}

function kindOf(filename: string): string {
  const name = filename.toLowerCase();
  if (name.includes("transcrip") || name.endsWith(".vtt") || name.endsWith(".srt")) return "transcripcion";
  if (name.includes("cuestionario")) return "cuestionario";
  if (name.includes("pantalla")) return "pantallas";
  return "especificacion";
}

export const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  me: (s: Session) => request<Identity>(s, "/api/me"),
  stages: (s: Session) => request<Stage[]>(s, "/api/stages"),
  list: (s: Session) => request<Requirement[]>(s, "/api/requirements"),
  detail: (s: Session, id: number) => request<RequirementDetail>(s, `/api/requirements/${id}`),
  create: (s: Session, data: NewRequirement) =>
    request<Requirement>(s, "/api/requirements", post(data)),
  answer: (s: Session, id: number, messageId: number, body: string) =>
    request<void>(s, `/api/requirements/${id}/answers`, post({ message_id: messageId, body })),
  decide: (s: Session, id: number, outcome: Outcome, comment: string) =>
    request<void>(s, `/api/requirements/${id}/decisions`, post({ outcome, comment })),
  createWithFiles: (s: Session, data: NewRequirement, files: File[]) =>
    request<Requirement>(s, "/api/requirements/with-files", {
      method: "POST",
      body: formWith(
        [...files, ...data.documents.map((d) => new File([d.content], `${d.name}.txt`))],
        { title: data.title, description: data.description, project: data.project },
      ),
    }),
  file: (s: Session, id: number, path: string) =>
    request<RepoFile>(s, `/api/requirements/${id}/file?path=${encodeURIComponent(path)}`),
  documents: (s: Session, id: number) =>
    request<DocumentFile[]>(s, `/api/requirements/${id}/documents`),
  addDocuments: (s: Session, id: number, files: File[]) =>
    request<DocumentFile[]>(s, `/api/requirements/${id}/documents`, {
      method: "POST",
      body: formWith(files, {}),
    }),
  heartbeat: (s: Session, requirementId: number | null) =>
    request<void>(s, "/api/time/heartbeat", post({ requirement_id: requirementId })),
  time: (s: Session) => request<TimeRow[]>(s, "/api/time"),
  portfolio: (s: Session) => request<Portfolio>(s, "/api/portfolio"),
  resume: (s: Session, id: number) =>
    request<void>(s, `/api/requirements/${id}/resume`, { method: "POST" }),
};
