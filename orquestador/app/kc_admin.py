"""Lectura mínima de la Keycloak Admin API para el Inbox.

El Panel es quien ESCRIBE el atributo `proyectos` de cada usuario (segmentación
por proyecto OpenProject). Aquí solo lo LEEMOS, por `sub`, para filtrar qué
requisitos ve cada usuario. Sin mapper en el token (decisión: opción B) → se
consulta la Admin API. Cacheamos el token de admin para no re-autenticar en
cada request del Inbox.
"""
from __future__ import annotations

import time

import httpx

import config

_KC_BASE = config.OIDC_ISSUER.rsplit("/realms/", 1)[0]
_REALM = config.OIDC_ISSUER.rsplit("/realms/", 1)[1]

# Cache simple del token de admin (token, expira_en_epoch).
_tok_cache: tuple[str, float] = ("", 0.0)


async def _admin_token(c: httpx.AsyncClient) -> str:
    global _tok_cache
    tok, exp = _tok_cache
    if tok and time.time() < exp - 10:
        return tok
    r = await c.post(
        f"{_KC_BASE}/realms/master/protocol/openid-connect/token",
        data={"client_id": "admin-cli", "grant_type": "password",
              "username": config.KC_ADMIN_USER,
              "password": config.KC_ADMIN_PASSWORD},
    )
    r.raise_for_status()
    data = r.json()
    tok = data["access_token"]
    _tok_cache = (tok, time.time() + float(data.get("expires_in", 60)))
    return tok


async def _get_user_attr(sub: str, attr: str) -> list[str]:
    """Lee un atributo multivaluado del usuario por sub. Fail-open: [] si falla."""
    if not sub or not config.OIDC_ISSUER:
        return []
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            tok = await _admin_token(c)
            r = await c.get(
                f"{_KC_BASE}/admin/realms/{_REALM}/users/{sub}",
                headers={"Authorization": f"Bearer {tok}"})
            if r.status_code != 200:
                return []
            vals = (r.json().get("attributes") or {}).get(attr) or []
            return [str(v).strip() for v in vals if str(v).strip()]
    except Exception:  # noqa: BLE001
        return []


async def get_user_projects(sub: str) -> list[str]:
    """IDs de proyecto OP asignados al usuario (atributo `proyectos`).
    Lista vacía si no tiene ninguno (fail-open: el filtro solo aplica con
    asignación explícita)."""
    return await _get_user_attr(sub, "proyectos")


async def get_user_capabilities(sub: str) -> list[str]:
    """IDs de WP Capability asignados al usuario (atributo `capacidades`).
    Segmentación fina dentro del proyecto. Vacío = sin restricción."""
    return await _get_user_attr(sub, "capacidades")


async def list_users_with_role(role: str) -> list[dict]:
    """Usuarios del realm que tienen el rol dado, con sus atributos
    proyectos/capacidades. Para poblar picklists de asignación (funcional /
    usuario-clave) filtrados por proyecto+capacidad. Fail-open: [] si falla."""
    if not role or not config.OIDC_ISSUER:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            tok = await _admin_token(c)
            H = {"Authorization": f"Bearer {tok}"}
            ru = await c.get(f"{_KC_BASE}/admin/realms/{_REALM}/users",
                             params={"max": 500, "briefRepresentation": "false"},
                             headers=H)
            allu = {u["id"]: u for u in ru.json()} if ru.status_code == 200 else {}
            rr = await c.get(f"{_KC_BASE}/admin/realms/{_REALM}/roles/{role}/users",
                             params={"max": 500}, headers=H)
            ids = [u["id"] for u in rr.json()] if rr.status_code == 200 else []
            out = []
            for uid in ids:
                u = allu.get(uid) or {}
                attrs = u.get("attributes") or {}
                nm = f"{u.get('firstName','')} {u.get('lastName','')}".strip()
                out.append({
                    "username": u.get("username"),
                    "name": nm or u.get("username"),
                    "proyectos": [str(x) for x in (attrs.get("proyectos") or [])],
                    "capacidades": [str(x) for x in (attrs.get("capacidades") or [])],
                })
            return out
    except Exception:  # noqa: BLE001
        return []


async def list_users_basic() -> list[dict]:
    """Todos los usuarios del realm con su nombre para mostrar: [{username, name}].
    Ligero (una llamada, sin roles). Para mapear username→Nombre Apellido en el
    Gantt. Fail-open: [] si falla."""
    if not config.OIDC_ISSUER:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            tok = await _admin_token(c)
            r = await c.get(f"{_KC_BASE}/admin/realms/{_REALM}/users",
                            params={"max": 500, "briefRepresentation": "true"},
                            headers={"Authorization": f"Bearer {tok}"})
            if r.status_code != 200:
                return []
            out = []
            for u in r.json():
                nm = f"{u.get('firstName','')} {u.get('lastName','')}".strip()
                out.append({"username": u.get("username"),
                            "name": nm or u.get("username")})
            return out
    except Exception:  # noqa: BLE001
        return []
