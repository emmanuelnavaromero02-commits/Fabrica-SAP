from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fabrica.catalog import StageKind, StageMachine, stage_machine
from fabrica.db.models import RunState

Outcome = Literal["approve", "reject", "discard"]


class TransitionError(ValueError): ...


@dataclass(frozen=True)
class Transition:
    stage: str
    state: RunState


def state_for(stage_key: str, machine: StageMachine | None = None) -> RunState:
    machine = machine or stage_machine()
    kind = machine.get(stage_key).kind
    return {
        StageKind.AUTO: RunState.RUNNING,
        StageKind.GATE: RunState.WAITING_GATE,
        StageKind.FINAL: RunState.DONE,
    }[kind]


def advance(stage_key: str, machine: StageMachine | None = None) -> Transition:
    machine = machine or stage_machine()
    stage = machine.get(stage_key)
    if stage.kind is not StageKind.AUTO or stage.next is None:
        raise TransitionError(f"La etapa {stage_key} no avanza automáticamente")
    return Transition(stage.next, state_for(stage.next, machine))


def decide(
    stage_key: str, outcome: Outcome, role: str, machine: StageMachine | None = None
) -> Transition:
    machine = machine or stage_machine()
    stage = machine.get(stage_key)

    if stage.kind is StageKind.FINAL:
        raise TransitionError("El requisito ya terminó")

    if outcome == "discard":
        if role not in machine.discard_roles:
            raise TransitionError(f"El rol {role} no puede desestimar")
        return Transition("desestimado", RunState.DONE)

    if stage.kind is not StageKind.GATE:
        raise TransitionError(f"La etapa {stage_key} no espera decisiones")
    if role not in stage.roles:
        raise TransitionError(f"El rol {role} no decide en {stage.label}")

    target = stage.next if outcome == "approve" else stage.on_reject
    if target is None:
        raise TransitionError(f"{stage.label} no define destino para {outcome}")
    return Transition(target, state_for(target, machine))
