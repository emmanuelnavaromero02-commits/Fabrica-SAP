"""Dependencias del API: identidad, sesión y runner.

Beta: la identidad llega en cabeceras X-Fabrica-User / X-Fabrica-Role.
Producción: reemplazar `identity` por la validación del token OIDC de Keycloak;
el resto del código ya recibe una `Identity` y no cambia.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from fabrica.domain.schemas import Identity
from fabrica.pipeline.runner import Runner

ROLES = {"admin", "lider", "funcional", "usuario_clave", "abap", "consultor"}


async def identity(
    x_fabrica_user: Annotated[str | None, Header()] = None,
    x_fabrica_role: Annotated[str | None, Header()] = None,
) -> Identity:
    if not x_fabrica_user or not x_fabrica_role:
        raise HTTPException(401, "Falta identidad (X-Fabrica-User / X-Fabrica-Role)")
    if x_fabrica_role not in ROLES:
        raise HTTPException(403, f"Rol desconocido: {x_fabrica_role}")
    return Identity(user=x_fabrica_user, role=x_fabrica_role)


def runner(request: Request) -> Runner:
    r: Runner = request.app.state.runner
    return r


Who = Annotated[Identity, Depends(identity)]
RunnerDep = Annotated[Runner, Depends(runner)]
