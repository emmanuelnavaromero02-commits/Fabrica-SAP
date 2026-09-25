from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from fabrica.auth.oidc import (
    KNOWN_ROLES,
    AuthenticationError,
    AuthorizationError,
    OidcVerifier,
    identity_from_claims,
)
from fabrica.config import get_settings
from fabrica.domain.schemas import Identity
from fabrica.pipeline.runner import Runner


def build_verifier() -> OidcVerifier:
    settings = get_settings()
    return OidcVerifier(
        settings.oidc_issuer, settings.oidc_audience, jwks_url=settings.oidc_jwks_url
    )


async def _from_token(request: Request, authorization: str | None, role: str | None) -> Identity:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Falta el token de acceso", {"WWW-Authenticate": "Bearer"})
    verifier: OidcVerifier = request.app.state.oidc
    try:
        claims = await verifier.claims(authorization.split(" ", 1)[1])
        return identity_from_claims(claims, get_settings().oidc_client_id, role)
    except AuthenticationError as exc:
        raise HTTPException(401, str(exc), {"WWW-Authenticate": "Bearer"}) from exc
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc


def _from_headers(user: str | None, role: str | None) -> Identity:
    if not user or not role:
        raise HTTPException(401, "Falta identidad (X-Fabrica-User / X-Fabrica-Role)")
    if role not in KNOWN_ROLES:
        raise HTTPException(403, f"Rol desconocido: {role}")
    return Identity(user=user, role=role, roles=[role])


async def identity(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_fabrica_user: Annotated[str | None, Header()] = None,
    x_fabrica_role: Annotated[str | None, Header()] = None,
) -> Identity:
    if get_settings().auth_mode == "oidc":
        return await _from_token(request, authorization, x_fabrica_role)
    return _from_headers(x_fabrica_user, x_fabrica_role)


def runner(request: Request) -> Runner:
    r: Runner = request.app.state.runner
    return r


Who = Annotated[Identity, Depends(identity)]
RunnerDep = Annotated[Runner, Depends(runner)]
