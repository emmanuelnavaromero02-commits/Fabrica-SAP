from __future__ import annotations

import os
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from pydantic import AnyHttpUrl

from fabrica.auth.oidc import AuthenticationError, OidcVerifier, roles_from_claims
from fabrica.config import get_settings


class McpTokenVerifier:
    def __init__(self, verifier: OidcVerifier, client_id: str) -> None:
        self.verifier = verifier
        self.client_id = client_id

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await self.verifier.claims(token)
        except AuthenticationError:
            return None
        if not roles_from_claims(claims, self.client_id):
            return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp", self.client_id)),
            scopes=str(claims.get("scope", "")).split(),
            expires_at=int(claims["exp"]),
            subject=str(claims["sub"]),
            claims=claims,
        )


def public_url(default_port: int) -> str:
    port = int(os.environ.get("FABRICA_MCP_PORT", default_port))
    return os.environ.get("FABRICA_MCP_PUBLIC_URL", f"http://localhost:{port}/mcp")


def auth_kwargs(default_port: int) -> dict[str, Any]:
    settings = get_settings()
    if settings.auth_mode != "oidc":
        return {}
    verifier = OidcVerifier(
        settings.oidc_issuer, settings.oidc_audience, jwks_url=settings.oidc_jwks_url
    )
    return {
        "token_verifier": McpTokenVerifier(verifier, settings.oidc_client_id),
        "auth": AuthSettings(
            issuer_url=AnyHttpUrl(settings.oidc_issuer),
            resource_server_url=AnyHttpUrl(public_url(default_port)),
            validate_token_resource=False,
        ),
    }


def actor() -> str:
    token = get_access_token()
    if token is not None and token.claims:
        claims = token.claims
        return str(claims.get("preferred_username") or claims.get("email") or token.subject)
    return os.environ.get("FABRICA_MCP_USER", "consultor")


def serve(server: MCPServer, default_port: int) -> None:
    transport = os.environ.get("FABRICA_MCP_TRANSPORT", "stdio")
    if transport == "http":
        import anyio

        anyio.run(
            lambda: server.run_streamable_http_async(
                host=os.environ.get("FABRICA_MCP_HOST", "127.0.0.1"),
                port=int(os.environ.get("FABRICA_MCP_PORT", default_port)),
            )
        )
    else:
        server.run("stdio")
