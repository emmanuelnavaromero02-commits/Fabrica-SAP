from __future__ import annotations

import argparse
import asyncio
import os
import secrets
from typing import Any

import httpx

from fabrica.auth.oidc import KNOWN_ROLES


class KeycloakAdminError(RuntimeError): ...


class KeycloakAdmin:
    def __init__(
        self,
        base_url: str,
        realm: str,
        admin_user: str,
        admin_password: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.realm = realm
        self.admin_user = admin_user
        self.admin_password = admin_password
        self.http = http or httpx.AsyncClient(timeout=20)
        self._token = ""

    async def _headers(self) -> dict[str, str]:
        if not self._token:
            resp = await self.http.post(
                f"{self.base}/realms/master/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": self.admin_user,
                    "password": self.admin_password,
                },
            )
            if resp.status_code != 200:
                raise KeycloakAdminError(
                    f"Credenciales de administrador rechazadas ({resp.status_code})"
                )
            self._token = str(resp.json()["access_token"])
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, path: str) -> str:
        return f"{self.base}/admin/realms/{self.realm}{path}"

    async def _user_id(self, username: str) -> str | None:
        resp = await self.http.get(
            self._url("/users"),
            params={"username": username, "exact": "true"},
            headers=await self._headers(),
        )
        resp.raise_for_status()
        users: list[dict[str, Any]] = resp.json()
        return str(users[0]["id"]) if users else None

    async def ensure_user(
        self, username: str, email: str, first_name: str, last_name: str, roles: list[str]
    ) -> tuple[str, str | None]:
        unknown = set(roles) - KNOWN_ROLES
        if unknown:
            raise KeycloakAdminError(f"Roles desconocidos: {', '.join(sorted(unknown))}")
        headers = await self._headers()
        user_id = await self._user_id(username)
        password = None
        if user_id is None:
            resp = await self.http.post(
                self._url("/users"),
                headers=headers,
                json={
                    "username": username,
                    "email": email,
                    "firstName": first_name,
                    "lastName": last_name,
                    "enabled": True,
                    "emailVerified": True,
                },
            )
            if resp.status_code != 201:
                raise KeycloakAdminError(f"No se pudo crear {username}: {resp.text[:200]}")
            user_id = resp.headers["Location"].rsplit("/", 1)[-1]
            password = secrets.token_urlsafe(12)
            await self.http.put(
                self._url(f"/users/{user_id}/reset-password"),
                headers=headers,
                json={"type": "password", "value": password, "temporary": True},
            )
        representations = []
        for role in roles:
            resp = await self.http.get(self._url(f"/roles/{role}"), headers=headers)
            resp.raise_for_status()
            representations.append(resp.json())
        resp = await self.http.post(
            self._url(f"/users/{user_id}/role-mappings/realm"),
            headers=headers,
            json=representations,
        )
        resp.raise_for_status()
        return user_id, password


def run() -> None:
    parser = argparse.ArgumentParser(prog="fabrica-usuario")
    parser.add_argument("--usuario", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--nombre", default="")
    parser.add_argument("--apellido", default="")
    parser.add_argument("--rol", action="append", required=True, choices=sorted(KNOWN_ROLES))
    args = parser.parse_args()
    admin = KeycloakAdmin(
        os.environ.get("FABRICA_KEYCLOAK_URL", "http://localhost:8080"),
        os.environ.get("FABRICA_KEYCLOAK_REALM", "fabrica"),
        os.environ["FABRICA_KEYCLOAK_ADMIN"],
        os.environ["FABRICA_KEYCLOAK_ADMIN_PASSWORD"],
    )
    user_id, password = asyncio.run(
        admin.ensure_user(args.usuario, args.email, args.nombre, args.apellido, args.rol)
    )
    print(f"Usuario {args.usuario} ({user_id}) con roles {', '.join(args.rol)}")
    if password:
        print(f"Contraseña temporal: {password}")
