// Tipos del API. Reflejan fabrica/domain/schemas.py del backend.

export type RunState = "running" | "waiting_gate" | "blocked" | "done";
export type Outcome = "approve" | "reject" | "discard";
export type Role = "admin" | "lider" | "funcional" | "usuario_clave" | "abap" | "consultor";

export type MessageKind =
  | "pregunta"
  | "respuesta"
  | "propuesta"
  | "objecion"
  | "evidencia"
  | "escalamiento"
  | "decision"
  | "info";

export interface Identity {
  user: string;
  role: Role;
}

export interface Requirement {
  id: number;
  title: string;
  description: string;
  project: string;
  capability: string | null;
  ricefw: string | null;
  stage: string;
  state: RunState;
  created_by: string;
  spent_usd: number;
  repo_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: number;
  thread: string;
  sender: string;
  recipient: string | null;
  kind: MessageKind;
  body: string;
  data: Record<string, unknown>;
  resolved: boolean;
  created_at: string;
}

export interface Attempt {
  id: number;
  activity: string;
  tier: string;
  provider: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  passed: boolean;
  issues: string[];
  created_at: string;
}

export interface Decision {
  id: number;
  stage: string;
  outcome: Outcome;
  actor: string;
  role: string;
  comment: string;
  created_at: string;
}

export interface Artifact {
  id: number;
  path: string;
  commit: string;
  author: string;
  created_at: string;
}

export interface RequirementDetail {
  requirement: Requirement;
  messages: Message[];
  attempts: Attempt[];
  decisions: Decision[];
  artifacts: Artifact[];
}

export interface Stage {
  key: string;
  label: string;
  kind: "auto" | "gate" | "final";
  roles: string[];
}

export interface NewRequirement {
  title: string;
  description: string;
  project: string;
  documents: { name: string; kind: string; content: string }[];
}
