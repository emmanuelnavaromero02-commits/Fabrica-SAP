from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from fabrica import config
from fabrica.api.app import create_app
from fabrica.auth.oidc import AuthenticationError, OidcVerifier
from fabrica.pipeline.runner import InlineRunner

ISSUER = "http://kc.test/realms/fabrica"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = {**json.loads(RSAAlgorithm.to_jwk(KEY.public_key())), "kid": "k1", "alg": "RS256"}


def _keycloak(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("openid-configuration"):
        return httpx.Response(200, json={"jwks_uri": f"{ISSUER}/certs"})
    return httpx.Response(200, json={"keys": [JWK]})


def token(**overrides: Any) -> str:
    claims = {
        "iss": ISSUER,
        "aud": "fabrica-api",
        "sub": "u-1",
        "exp": int(time.time()) + 300,
        "preferred_username": "ana",
        "realm_access": {"roles": ["funcional", "offline_access"]},
        **overrides,
    }
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("FABRICA_AUTH_MODE", "oidc")
    monkeypatch.setenv("FABRICA_OIDC_ISSUER", ISSUER)
    config.get_settings.cache_clear()
    app = create_app()
    app.state.runner = InlineRunner()
    http = httpx.AsyncClient(transport=httpx.MockTransport(_keycloak))
    app.state.oidc = OidcVerifier(ISSUER, "fabrica-api", http=http)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def bearer(value: str, role: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {value}"}
    if role:
        headers["X-Fabrica-Role"] = role
    return headers


async def test_valid_token_yields_identity_with_roles(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/me", headers=bearer(token()))
    assert resp.status_code == 200
    assert resp.json() == {"user": "ana", "role": "funcional", "roles": ["funcional"]}


async def test_missing_or_forged_tokens_are_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/me")).status_code == 401
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode({"iss": ISSUER}, other_key, algorithm="RS256", headers={"kid": "k1"})
    assert (await client.get("/api/me", headers=bearer(forged))).status_code == 401
    assert (await client.get("/api/me", headers=bearer(token(aud="otra")))).status_code == 401
    expired = token(exp=int(time.time()) - 10)
    assert (await client.get("/api/me", headers=bearer(expired))).status_code == 401


async def test_roles_come_from_the_token(client: httpx.AsyncClient) -> None:
    no_roles = token(realm_access={"roles": []})
    assert (await client.get("/api/me", headers=bearer(no_roles))).status_code == 403
    resp = await client.get("/api/me", headers=bearer(token(), role="abap"))
    assert resp.status_code == 403
    multi = token(realm_access={"roles": ["usuario_clave", "lider"]})
    resp = await client.get("/api/me", headers=bearer(multi, role="usuario_clave"))
    assert resp.json()["role"] == "usuario_clave"
    assert resp.json()["roles"] == ["lider", "usuario_clave"]


async def test_headers_cannot_impersonate_in_oidc_mode(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/me", headers={"X-Fabrica-User": "x", "X-Fabrica-Role": "admin"})
    assert resp.status_code == 401


async def test_auth_config_is_public(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/auth/config")
    assert resp.json() == {"mode": "oidc", "issuer": ISSUER, "client_id": "fabrica-web"}


async def test_mcp_token_verifier_requires_factory_roles() -> None:
    from fabrica.mcp_servers.common import McpTokenVerifier

    http = httpx.AsyncClient(transport=httpx.MockTransport(_keycloak))
    verifier = McpTokenVerifier(OidcVerifier(ISSUER, "fabrica-api", http=http), "fabrica-web")
    access = await verifier.verify_token(token())
    assert access is not None and access.subject == "u-1"
    assert await verifier.verify_token(token(realm_access={"roles": []})) is None
    assert await verifier.verify_token("basura") is None


async def test_symmetric_or_unsigned_tokens_are_rejected(client: httpx.AsyncClient) -> None:
    claims = {"iss": ISSUER, "aud": "fabrica-api", "sub": "u-1", "exp": int(time.time()) + 60}
    hs = jwt.encode(claims, "x" * 32, algorithm="HS256", headers={"kid": "k1"})
    unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": "k1"})
    assert (await client.get("/api/me", headers=bearer(hs))).status_code == 401
    assert (await client.get("/api/me", headers=bearer(unsigned))).status_code == 401


async def test_unknown_key_ids_do_not_hammer_the_issuer() -> None:
    fetches: list[str] = []

    def counting(request: httpx.Request) -> httpx.Response:
        fetches.append(request.url.path)
        return _keycloak(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(counting))
    verifier = OidcVerifier(ISSUER, "fabrica-api", jwks_url=f"{ISSUER}/certs", http=http)
    unknown = jwt.encode({"sub": "x"}, KEY, algorithm="RS256", headers={"kid": "otra"})
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            await verifier.claims(unknown)
    assert len(fetches) == 2
