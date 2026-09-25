import pytest

from fabrica.db.models import RunState
from fabrica.domain.stages import TransitionError, advance, decide


def test_auto_stage_advances_to_next() -> None:
    assert advance("recepcion").stage == "diseno"
    t = advance("diseno")
    assert (t.stage, t.state) == ("aprobacion_cliente", RunState.WAITING_GATE)


def test_gate_approve_and_reject() -> None:
    assert decide("aprobacion_cliente", "approve", "funcional").stage == "construccion"
    assert decide("aprobacion_cliente", "reject", "usuario_clave").stage == "diseno"
    assert decide("uat", "approve", "usuario_clave").state is RunState.DONE


def test_wrong_role_cannot_decide() -> None:
    with pytest.raises(TransitionError):
        decide("revision_abap", "approve", "funcional")
    with pytest.raises(TransitionError):
        decide("uat", "approve", "abap")  # la fábrica no aprueba la UAT del cliente


def test_discard_requires_leader() -> None:
    assert decide("diseno", "discard", "lider").stage == "desestimado"
    with pytest.raises(TransitionError):
        decide("diseno", "discard", "funcional")


def test_auto_stage_does_not_accept_decisions() -> None:
    with pytest.raises(TransitionError):
        decide("construccion", "approve", "admin")
