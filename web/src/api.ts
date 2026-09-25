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

async function request<T>(who: Identity, path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Fabrica-User": who.user,
      "X-Fabrica-Role": who.role,
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

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  stages: (who: Identity) => request<Stage[]>(who, "/api/stages"),
  list: (who: Identity) => request<Requirement[]>(who, "/api/requirements"),
  detail: (who: Identity, id: number) => request<RequirementDetail>(who, `/api/requirements/${id}`),
  create: (who: Identity, data: NewRequirement) =>
    request<Requirement>(who, "/api/requirements", post(data)),
  answer: (who: Identity, id: number, messageId: number, body: string) =>
    request<void>(who, `/api/requirements/${id}/answers`, post({ message_id: messageId, body })),
  decide: (who: Identity, id: number, outcome: Outcome, comment: string) =>
    request<void>(who, `/api/requirements/${id}/decisions`, post({ outcome, comment })),
  resume: (who: Identity, id: number) =>
    request<void>(who, `/api/requirements/${id}/resume`, { method: "POST" }),
};
