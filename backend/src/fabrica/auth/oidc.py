from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
from jwt import PyJWKSet

from fabrica.domain.schemas import Identity

ROLE_PRIORITY = ("admin", "lider", "abap", "funcional", "usuario_clave", "consultor")
KNOWN_ROLES = frozenset(ROLE_PRIORITY)
SIGNING_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384")
FORCED_REFRESH_INTERVAL = 30.0


class AuthenticationError(Exception): ...


class AuthorizationError(Exception): ...


class OidcVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        jwks_url: str = "",
        http: httpx.AsyncClient | None = None,
        cache_seconds: float = 300,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.jwks_url = jwks_url
        self.http = http or httpx.AsyncClient(timeout=10)
        self.cache_seconds = cache_seconds
        self._keys: PyJWKSet | None = None
        self._loaded_at = 0.0
        self._forced_at = float("-inf")

    async def _discover_jwks_url(self) -> str:
        if self.jwks_url:
            return self.jwks_url
        resp = await self.http.get(f"{self.issuer}/.well-known/openid-configuration")
        resp.raise_for_status()
        self.jwks_url = str(resp.json()["jwks_uri"])
        return self.jwks_url

    async def _load_keys(self, force: bool = False) -> PyJWKSet:
        fresh = time.monotonic() - self._loaded_at < self.cache_seconds
        if self._keys is not None and fresh and not force:
            return self._keys
        if force and self._keys is not None:
            if time.monotonic() - self._forced_at < FORCED_REFRESH_INTERVAL:
                return self._keys
            self._forced_at = time.monotonic()
        resp = await self.http.get(await self._discover_jwks_url())
        resp.raise_for_status()
        self._keys = PyJWKSet.from_dict(resp.json())
        self._loaded_at = time.monotonic()
        return self._keys

    async def _key_for(self, kid: str) -> jwt.PyJWK:
        for force in (False, True):
            keys = await self._load_keys(force=force)
            for key in keys.keys:
                if key.key_id == kid:
                    return key
        raise AuthenticationError("Clave de firma desconocida")

    async def claims(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in SIGNING_ALGORITHMS:
                raise AuthenticationError("Algoritmo de firma no permitido")
            key = await self._key_for(str(header.get("kid", "")))
            decoded: dict[str, Any] = jwt.decode(
                token,
                key.key,
                algorithms=[str(header["alg"])],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "sub"]},
            )
        except (jwt.PyJWTError, TypeError, ValueError) as exc:
            raise AuthenticationError(f"Token inválido: {exc}") from exc
        except httpx.HTTPError as exc:
            raise AuthenticationError("No se pudo obtener las claves del emisor") from exc
        return decoded


def roles_from_claims(claims: dict[str, Any], client_id: str) -> list[str]:
    realm = claims.get("realm_access", {}).get("roles", [])
    client = claims.get("resource_access", {}).get(client_id, {}).get("roles", [])
    granted = {r for r in [*realm, *client] if r in KNOWN_ROLES}
    return [r for r in ROLE_PRIORITY if r in granted]


def identity_from_claims(
    claims: dict[str, Any], client_id: str, requested_role: str | None = None
) -> Identity:
    roles = roles_from_claims(claims, client_id)
    if not roles:
        raise AuthorizationError("El usuario no tiene roles de la fábrica")
    if requested_role and requested_role not in roles:
        raise AuthorizationError(f"El usuario no tiene el rol {requested_role}")
    user = str(claims.get("preferred_username") or claims.get("email") or claims["sub"])
    return Identity(user=user, role=requested_role or roles[0], roles=roles)
