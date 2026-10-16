"""OIDC code flow contra Keycloak — login, callback, logout, middleware.

El inbox redirige a /auth/login → Keycloak login → /auth/callback → sesión
firmada en cookie. Los endpoints `/api/*` requieren sesión (middleware).
La identidad (sub, username, email, roles) se expone via `get_current_user`.

Si OIDC_ISSUER no está configurado o Keycloak no responde, el módulo cae en
modo dev — usuario sintético "dev" con todos los roles.
"""
from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import URLSafeTimedSerializer, BadSignature

import config

log = logging.getLogger("orquestador.auth")
router = APIRouter()

_SESSION_COOKIE = "fabro_session"
_SESSION_MAX_AGE = 8 * 3600  # 8 horas

_serializer = URLSafeTimedSerializer(config.SESSION_SECRET, salt="fabro-session")

# Endpoints OIDC: derivados directamente del issuer interno (Keycloak tiene
# paths estables). NO usamos /.well-known/openid-configuration porque
# Keycloak en modo dev anuncia URLs basadas en el header Host de la request,
# y desde la red Docker interna el Host es "keycloak:8080" → discovery
# devuelve URLs con localhost:8080 que NO resuelven en el container.
def _internal_token_endpoint() -> str:
    return f"{config.OIDC_ISSUER}/protocol/openid-connect/token"


def _public_authorize_url(state: str) -> str:
    """URL al que redirigir el browser para iniciar el login."""
    base = f"{config.OIDC_PUBLIC_ISSUER}/protocol/openid-connect/auth"
    params = {
        "client_id": config.OIDC_CLIENT_ID,
        "redirect_uri": f"{config.INBOX_BASE_URL}/auth/callback",
        "response_type": "code",
        "scope": "openid profile email roles",
        "state": state,
    }
    return f"{base}?{urlencode(params)}"


def _public_logout_url(id_token_hint: str | None = None) -> str:
    """KC 25 requiere id_token_hint para no pedir confirmación. Si no lo
    tenemos (ej. cookie inválida) solo redirigimos al endpoint sin hint y
    KC mostrará su pantalla de confirmación — no es 404.

    Redirigimos post-logout a /inbox (no a la raíz) porque la app no
    sirve la ruta /."""
    base = f"{config.OIDC_PUBLIC_ISSUER}/protocol/openid-connect/logout"
    params: dict = {
        "post_logout_redirect_uri": f"{config.INBOX_BASE_URL}/inbox",
        "client_id": config.OIDC_CLIENT_ID,
    }
    if id_token_hint:
        params["id_token_hint"] = id_token_hint
    return f"{base}?{urlencode(params)}"


async def _exchange_code(code: str) -> dict:
    """Intercambia el code por tokens. Usa endpoint INTERNO (entre containers)."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(_internal_token_endpoint(), data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": config.OIDC_CLIENT_ID,
            "client_secret": config.OIDC_CLIENT_SECRET,
            "redirect_uri": f"{config.INBOX_BASE_URL}/auth/callback",
        })
        if r.status_code >= 400:
            raise RuntimeError(
                f"Token exchange falló: HTTP {r.status_code} {r.text[:200]}")
        return r.json()


def _decode_unverified(jwt_token: str) -> dict:
    """Decodifica el JWT SIN verificar firma. Seguro en NUESTRO uso porque
    el token vino directo del IdP via /token endpoint (TLS interno en
    producción, red Docker en local). Para verificación de firma estricta
    usar python-jose con JWKS — overkill para este flujo cerrado."""
    import base64, json as _json
    parts = jwt_token.split(".")
    if len(parts) != 3:
        return {}
    pad = "=" * (-len(parts[1]) % 4)
    return _json.loads(base64.urlsafe_b64decode(parts[1] + pad))


def _make_user_from_tokens(tokens: dict) -> dict:
    """Extrae identidad y roles de los tokens del IdP.

    Keycloak por defecto incluye `realm_access.roles` SOLO en el
    access_token (no en el id_token). Por eso decodificamos AMBOS:
      - id_token  → identidad (sub, email, name, preferred_username)
      - access_token → realm_access.roles

    Guardamos también el id_token compactado para usarlo como
    `id_token_hint` en el logout (KC 25 lo requiere para no mostrar
    pantalla de confirmación).

    No llamamos a /userinfo (evita problemas de hostname mismatch cuando
    KC en modo dev ve el Host header del request)."""
    id_token = tokens.get("id_token", "")
    id_claims = _decode_unverified(id_token)
    ac_claims = _decode_unverified(tokens.get("access_token", ""))
    # Identidad: preferimos id_token, fallback access_token.
    ident = id_claims if id_claims else ac_claims
    # Roles: realm_access vive en el access_token de KC por default.
    realm_roles = ((ac_claims.get("realm_access") or {}).get("roles")
                   or (id_claims.get("realm_access") or {}).get("roles")
                   or [])
    roles = [r for r in realm_roles
             if r not in ("offline_access", "uma_authorization",
                          "default-roles-fabrica")]
    return {
        "sub": ident.get("sub"),
        "username": ident.get("preferred_username"),
        "email": ident.get("email"),
        "name": ident.get("name") or ident.get("preferred_username"),
        "roles": roles,
        "id_token": id_token,            # usado en logout como hint
        "issued_at": int(time.time()),
    }


def _make_session_cookie(user: dict) -> str:
    return _serializer.dumps(user)


def _read_session_cookie(token: str) -> dict | None:
    try:
        return _serializer.loads(token, max_age=_SESSION_MAX_AGE)
    except BadSignature:
        return None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@router.get("/auth/login")
async def auth_login(request: Request, next: str = "/inbox"):
    """Inicia el flujo OIDC. `next` = a dónde regresar tras login."""
    state = _serializer.dumps({"next": next})
    return RedirectResponse(_public_authorize_url(state), status_code=302)


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = "",
                        error: str = ""):
    """Callback del IdP: intercambia code por tokens y guarda sesión."""
    if error:
        raise HTTPException(status_code=400, detail=f"OIDC error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="falta code")
    next_url = "/inbox"
    try:
        st = _serializer.loads(state, max_age=600)
        next_url = st.get("next", "/inbox")
    except Exception:  # noqa: BLE001
        pass
    try:
        tokens = await _exchange_code(code)
        user = _make_user_from_tokens(tokens)
    except Exception as exc:  # noqa: BLE001
        log.exception("Auth callback falló")
        raise HTTPException(status_code=500, detail=f"auth: {exc}") from exc
    resp = RedirectResponse(next_url, status_code=302)
    resp.set_cookie(
        _SESSION_COOKIE,
        _make_session_cookie(user),
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # True en producción con HTTPS
    )
    log.info("Login OK: user=%s roles=%s", user.get("username"), user.get("roles"))
    return resp


@router.get("/auth/logout")
async def auth_logout(request: Request):
    """Borra la sesión local y redirige al logout del IdP."""
    user = get_current_user(request)
    id_token = user.get("id_token") if user else None
    resp = RedirectResponse(_public_logout_url(id_token), status_code=302)
    resp.delete_cookie(_SESSION_COOKIE)
    return resp


@router.get("/auth/sso-logout")
async def auth_sso_logout(request: Request):
    """Front-channel logout: Keycloak carga esta URL en un iframe oculto
    cuando el usuario hace logout en CUALQUIER cliente OIDC del realm.
    Aquí simplemente borramos la cookie de sesión propia para que la
    próxima request al inbox dispare re-login. NO redirige (KC no quiere
    nada de body — solo 200 OK).

    Especificación OIDC Frontchannel Logout 1.0:
    https://openid.net/specs/openid-connect-frontchannel-1_0.html
    """
    resp = Response(status_code=200, content="")
    resp.delete_cookie(_SESSION_COOKIE)
    return resp


@router.get("/auth/me")
async def auth_me(request: Request) -> dict:
    """Quién está logueado (sirve para el header del inbox)."""
    user = get_current_user(request)
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "user": user}


# --------------------------------------------------------------------------
# Helpers usados por el resto de la app
# --------------------------------------------------------------------------
def get_current_user(request: Request) -> dict | None:
    """Devuelve el usuario actual o None si no hay sesión.

    Modo dev: si OIDC_ISSUER está vacío, devuelve un usuario sintético con
    rol admin — útil para correr el inbox sin Keycloak (legado)."""
    if not config.OIDC_ISSUER:
        return {
            "sub": "dev", "username": "dev", "email": "dev@local",
            "name": "Dev (sin auth)", "roles": ["admin", "swf-funcional"],
        }
    cookie = request.cookies.get(_SESSION_COOKIE)
    if not cookie:
        return None
    return _read_session_cookie(cookie)


def require_user(request: Request) -> dict:
    """Como get_current_user pero levanta 401 si no hay sesión."""
    u = get_current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="not authenticated")
    return u


def require_role(request: Request, *roles: str) -> dict:
    """Requiere sesión Y que el usuario tenga al menos uno de los roles."""
    u = require_user(request)
    user_roles = set(u.get("roles") or [])
    if "admin" in user_roles:
        return u   # admin pasa todo
    if not (user_roles & set(roles)):
        raise HTTPException(status_code=403,
                            detail=f"requiere rol: {' | '.join(roles)}")
    return u
