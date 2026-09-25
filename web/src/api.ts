import type { Session } from "./auth";
import type {
  Identity,
  NewRequirement,
  Outcome,
  Requirement,
  RequirementDetail,
  Stage,
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
  resume: (s: Session, id: number) =>
    request<void>(s, `/api/requirements/${id}/resume`, { method: "POST" }),
};
