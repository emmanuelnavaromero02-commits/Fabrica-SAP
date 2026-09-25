import type { Stage } from "../types";

const ICON: Record<Stage["kind"], string> = { auto: "🤖", gate: "👤", final: "🏁" };

export function StageTimeline({ stages, current }: { stages: Stage[]; current: string }) {
  const visible = stages.filter((s) => s.key !== "desestimado" || s.key === current);
  const index = visible.findIndex((s) => s.key === current);
  return (
    <ol className="timeline">
      {visible.map((s, i) => (
        <li
          key={s.key}
          className={i < index ? "done" : i === index ? "current" : "pending"}
          title={s.roles.length ? `Decide: ${s.roles.join(", ")}` : undefined}
        >
          <span>{ICON[s.kind]}</span> {s.label}
        </li>
      ))}
    </ol>
  );
}
