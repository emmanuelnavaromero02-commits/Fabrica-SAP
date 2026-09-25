import type { Message, Stage } from "../../types";

const ICON: Record<Stage["kind"], string> = { auto: "🤖", gate: "👤", final: "🏁" };

function enteredAt(messages: Message[], stage: Stage): string | null {
  const hit = messages.find((m) => m.thread === "flujo" && m.body.endsWith(`→ ${stage.label}`));
  return hit ? new Date(hit.created_at).toLocaleString() : null;
}

export function Stepper({ stages, current, messages }: { stages: Stage[]; current: string; messages: Message[] }) {
  const visible = stages.filter((s) => s.key !== "desestimado" || s.key === current);
  const index = visible.findIndex((s) => s.key === current);
  return (
    <ol className="stepper" aria-label="Etapas">
      {visible.map((s, i) => {
        const state = i < index ? "done" : i === index ? "current" : "pending";
        const at = enteredAt(messages, s);
        return (
          <li key={s.key} className={`step ${state}`} title={at ? `Desde ${at}` : s.roles.length ? `Decide: ${s.roles.join(", ")}` : undefined}>
            <span aria-hidden>{state === "done" ? "✓" : ICON[s.kind]}</span>
            {s.label}
          </li>
        );
      })}
    </ol>
  );
}
