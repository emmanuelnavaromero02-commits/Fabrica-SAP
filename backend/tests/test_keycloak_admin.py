from __future__ import annotations

import json

import httpx
import pytest

from fabrica.auth.keycloak_admin import KeycloakAdmin, KeycloakAdminError

BASE = "http://kc.test"


class FakeKeycloak:
    def __init__(self) -> None:
        self.users: dict[str, str] = {}
        self.passwords: dict[str, dict[str, object]] = {}
        self.mappings: dict[str, list[str]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/protocol/openid-connect/token"):
            ok = b"password=secreto" in request.content
            return httpx.Response(200 if ok else 401, json={"access_token": "t"})
        assert request.headers["Authorization"] == "Bearer t"
        if path == "/admin/realms/fabrica/users" and request.method == "GET":
            name = request.url.params["username"]
            found = [{"id": self.users[name]}] if name in self.users else []
            return httpx.Response(200, json=found)
        if path == "/admin/realms/fabrica/users" and request.method == "POST":
            body = json.loads(request.content)
            self.users[body["username"]] = f"id-{body['username']}"
            location = f"{BASE}/admin/realms/fabrica/users/id-{body['username']}"
            return httpx.Response(201, headers={"Location": location})
        if path.endswith("/reset-password"):
            self.passwords[path.split("/")[-2]] = json.loads(request.content)
            return httpx.Response(204)
        if path.startswith("/admin/realms/fabrica/roles/"):
            return httpx.Response(200, json={"name": path.rsplit("/", 1)[-1]})
        if path.endswith("/role-mappings/realm"):
            self.mappings[path.split("/")[-3]] = [r["name"] for r in json.loads(request.content)]
            return httpx.Response(204)
        return httpx.Response(500)


def admin(fake: FakeKeycloak, password: str = "secreto") -> KeycloakAdmin:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return KeycloakAdmin(BASE, "fabrica", "admin", password, http)


async def test_creates_user_with_temporary_password_and_roles() -> None:
    fake = FakeKeycloak()
    user_id, password = await admin(fake).ensure_user("ana", "ana@x.pe", "Ana", "Q", ["funcional"])
    assert user_id == "id-ana" and password
    assert fake.passwords["id-ana"] == {"type": "password", "value": password, "temporary": True}
    assert fake.mappings["id-ana"] == ["funcional"]

    again, second_password = await admin(fake).ensure_user("ana", "ana@x.pe", "", "", ["lider"])
    assert again == "id-ana" and second_password is None
    assert fake.mappings["id-ana"] == ["lider"]


async def test_rejects_unknown_roles_and_bad_admin_credentials() -> None:
    with pytest.raises(KeycloakAdminError):
        await admin(FakeKeycloak()).ensure_user("x", "x@x", "", "", ["jefe"])
    with pytest.raises(KeycloakAdminError):
        await admin(FakeKeycloak(), "mala").ensure_user("x", "x@x", "", "", ["abap"])
