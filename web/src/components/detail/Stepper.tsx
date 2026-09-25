import { STAGE_ICON } from "../../stageIcons";
import type { Message, Stage } from "../../types";

const DECISION = /^(approve|reject|discard) en (.+?)(?::|$)/;

export function stageEntries(messages: Message[], stages: Stage[]): Map<string, string> {
  const byLabel = new Map(stages.map((s) => [s.label, s]));
  const entries = new Map<string, string>();
  for (const m of messages) {
    if (m.thread !== "flujo") continue;
    const arrow = m.body.split("→ ")[1];
    if (m.kind === "info" && arrow) {
      const target = byLabel.get(arrow.trim());
      if (target) entries.set(target.key, m.created_at);
      continue;
    }
    const decision = m.kind === "decision" ? DECISION.exec(m.body) : null;
    if (!decision) continue;
    const [, outcome, label] = decision;
    const from = label ? byLabel.get(label.trim()) : undefined;
    const target = outcome === "discard" ? "desestimado" : outcome === "approve" ? from?.next : from?.on_reject;
    if (target) entries.set(target, m.created_at);
  }
  return entries;
}

export function Stepper({ stages, current, messages }: { stages: Stage[]; current: string; messages: Message[] }) {
  const visible = stages.filter((s) => s.key !== "desestimado" || s.key === current);
  const index = visible.findIndex((s) => s.key === current);
  const entries = stageEntries(messages, stages);
  return (
    <ol className="stepper" aria-label="Etapas">
      {visible.map((s, i) => {
        const state = i < index ? "done" : i === index ? "current" : "pending";
        const at = entries.get(s.key);
        const title = at
          ? `Desde ${new Date(at).toLocaleString()}`
          : s.roles.length
            ? `Decide: ${s.roles.join(", ")}`
            : undefined;
        return (
          <li key={s.key} className={`step ${state}`} title={title}>
            <span aria-hidden>{state === "done" ? "✓" : STAGE_ICON[s.kind]}</span>
            {s.label}
          </li>
        );
      })}
    </ol>
  );
}
