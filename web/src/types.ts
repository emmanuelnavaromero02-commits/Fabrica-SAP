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
  roles?: Role[];
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

export interface Transport {
  id: number;
  system: string;
  number: string;
  objects: string[];
  status: string;
  created_at: string;
}

export interface Estimate {
  id: number;
  items: { object: string; size: string; rationale: string; hours: number }[];
  breakdown: Record<string, number>;
  assumptions: string[];
  hours_base: number;
  hours_total: number;
  days: number;
  complexity: string;
  created_at: string;
}

export interface DocumentFile {
  id: number;
  name: string;
  kind: string;
  content: string;
}

export interface SapCall {
  id: number;
  system: string;
  tool: string;
  object_name: string;
  actor: string;
  ok: boolean;
  detail: string;
  created_at: string;
}

export interface RepoFile {
  path: string;
  content: string;
}

export interface RequirementDetail {
  requirement: Requirement;
  messages: Message[];
  attempts: Attempt[];
  decisions: Decision[];
  artifacts: Artifact[];
  transports: Transport[];
  estimate: Estimate | null;
  sap_calls: SapCall[];
}

export interface Stage {
  key: string;
  label: string;
  kind: "auto" | "gate" | "final";
  roles: string[];
  next: string | null;
  on_reject: string | null;
}

export interface NewRequirement {
  title: string;
  description: string;
  project: string;
  documents: { name: string; kind: string; content: string }[];
}

export interface TimeRow {
  user: string;
  requirement_id: number | null;
  hours: number;
  by_source: Record<string, number>;
}

export interface PortfolioItem {
  id: number;
  title: string;
  stage: string;
}

export interface Portfolio {
  total: number;
  by_stage: Record<string, number>;
  by_state: Record<string, number>;
  blocked: PortfolioItem[];
  waiting: PortfolioItem[];
  lead_time_days: number | null;
  first_pass_rate: Record<string, number>;
  ai_cost_usd: number;
  requirements: (PortfolioItem & {
    estimated_hours: number | null;
    worked_hours: number;
    ai_cost_usd: number;
  })[];
}
