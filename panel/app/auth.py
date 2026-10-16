"""OIDC code flow contra Keycloak — copia adaptada del inbox.

Diferencia vs inbox:
- Restringe acceso a roles de la fábrica interna (admin, swf-funcional,
  swf-desarrollador, swf-tester). Funcionales del cliente y usuarios-clave
  reciben 403 porque el panel NO es para ellos — solo usan el inbox.
- Comparte SSO con el inbox (mismo IdP, mismo realm) — el navegador hereda
  la sesión KC y entra sin re-login.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import URLSafeTimedSerializer, BadSignature

import config

log = logging.getLogger("panel.auth")
router = APIRouter()

_SESSION_COOKIE = "fabro_panel_session"
_SESSION_MAX_AGE = 8 * 3600

_serializer = URLSafeTimedSerializer(config.SESSION_SECRET, salt="fabro-panel")


def _public_authorize_url(state: str) -> str:
    base = f"{config.OIDC_PUBLIC_ISSUER}/protocol/openid-connect/auth"
    params = {
        "client_id": config.OIDC_CLIENT_ID,
        "redirect_uri": f"{config.PANEL_BASE_URL}/auth/callback",
        "response_type": "code",
        "scope": "openid profile email roles",
        "state": state,
    }
    return f"{base}?{urlencode(params)}"


def _public_logout_url(id_token_hint: str | None = None) -> str:
    base = f"{config.OIDC_PUBLIC_ISSUER}/protocol/openid-connect/logout"
    params: dict = {
        "post_logout_redirect_uri": f"{config.PANEL_BASE_URL}/panel",
        "client_id": config.OIDC_CLIENT_ID,
    }
    if id_token_hint:
        params["id_token_hint"] = id_token_hint
    return f"{base}?{urlencode(params)}"


def _internal_token_endpoint() -> str:
    return f"{config.OIDC_ISSUER}/protocol/openid-connect/token"


async def _exchange_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(_internal_token_endpoint(), data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": config.OIDC_CLIENT_ID,
            "client_secret": config.OIDC_CLIENT_SECRET,
            "redirect_uri": f"{config.PANEL_BASE_URL}/auth/callback",
        })
        if r.status_code >= 400:
            raise RuntimeError(
                f"Token exchange falló: HTTP {r.status_code} {r.text[:200]}")
        return r.json()


def _decode_unverified(jwt_token: str) -> dict:
    parts = jwt_token.split(".")
    if len(parts) != 3:
        return {}
    pad = "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(parts[1] + pad))


def _make_user_from_tokens(tokens: dict) -> dict:
    id_token = tokens.get("id_token", "")
    id_claims = _decode_unverified(id_token)
    ac_claims = _decode_unverified(tokens.get("access_token", ""))
    ident = id_claims if id_claims else ac_claims
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
        "id_token": id_token,
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


@router.get("/auth/login")
async def auth_login(request: Request, next: str = "/panel"):
    state = _serializer.dumps({"next": next})
    return RedirectResponse(_public_authorize_url(state), status_code=302)


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = "",
                        error: str = ""):
    if error:
        raise HTTPException(status_code=400, detail=f"OIDC error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="falta code")
    next_url = "/panel"
    try:
        st = _serializer.loads(state, max_age=600)
        next_url = st.get("next", "/panel")
    except Exception:  # noqa: BLE001
        pass
    try:
        tokens = await _exchange_code(code)
        user = _make_user_from_tokens(tokens)
    except Exception as exc:  # noqa: BLE001
        log.exception("Auth callback falló")
        raise HTTPException(status_code=500, detail=f"auth: {exc}") from exc
    # Validar rol antes de crear sesión: el panel solo es para admin/swf.
    user_roles = set(user.get("roles") or [])
    if not (user_roles & config.ALLOWED_ROLES):
        log.warning("Login rechazado: user=%s roles=%s (sin rol de fábrica)",
                    user.get("username"), user_roles)
        # Devuelve HTML simple en vez de JSON para que se vea en el browser.
        return Response(
            status_code=403,
            content=(
                f"<html><body style='font: 14px system-ui; padding: 40px; "
                f"max-width: 600px; margin: auto'>"
                f"<h1>Acceso denegado</h1>"
                f"<p>El usuario <b>{user.get('username')}</b> "
                f"(roles: {', '.join(sorted(user_roles)) or '(ninguno)'}) "
                f"no tiene permiso para entrar al panel de control.</p>"
                f"<p>Se requiere uno de: <b>{', '.join(sorted(config.ALLOWED_ROLES))}</b>.</p>"
                f"<p>Pide a un admin que te asigne el rol en Keycloak.</p>"
                f"<p><a href='/auth/logout'>Cerrar sesión</a></p>"
                f"</body></html>"
            ),
            media_type="text/html",
        )
    resp = RedirectResponse(next_url, status_code=302)
    resp.set_cookie(
        _SESSION_COOKIE,
        _make_session_cookie(user),
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    log.info("Panel login OK: user=%s roles=%s",
             user.get("username"), user.get("roles"))
    return resp


@router.get("/auth/logout")
async def auth_logout(request: Request):
    user = get_current_user(request)
    id_token = user.get("id_token") if user else None
    resp = RedirectResponse(_public_logout_url(id_token), status_code=302)
    resp.delete_cookie(_SESSION_COOKIE)
    return resp


@router.get("/auth/sso-logout")
async def auth_sso_logout():
    """Front-channel logout invocado por KC en iframe oculto."""
    resp = Response(status_code=200, content="")
    resp.delete_cookie(_SESSION_COOKIE)
    return resp


@router.get("/auth/me")
async def auth_me(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "user": user}


def get_current_user(request: Request) -> dict | None:
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
    u = get_current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="not authenticated")
    return u


def require_panel_access(request: Request) -> dict:
    """Requiere sesión + uno de los roles permitidos."""
    u = require_user(request)
    if not (set(u.get("roles") or []) & config.ALLOWED_ROLES):
        raise HTTPException(status_code=403,
                            detail=f"requiere rol: {' | '.join(config.ALLOWED_ROLES)}")
    return u
