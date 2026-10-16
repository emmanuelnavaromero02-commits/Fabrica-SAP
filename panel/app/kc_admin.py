"""Cliente de la Keycloak Admin API para administrar usuarios del realm
'fabrica' desde el Panel. Keycloak es la fuente de verdad de identidad + roles
(SSO). El provisioning en OpenProject + Gitea (pre-link OIDC) es un paso
posterior — ver [[op-simplificado-detalle-temporal]] y el plan en el Panel.

Auth: password grant contra el realm master con client admin-cli (KC_ADMIN_*).
"""
from __future__ import annotations

import httpx

import config

# Endpoint interno de Keycloak (el panel corre en un container; usa el mismo
# hostname kc.fabrica.local que el resto, resuelto por extra_hosts).
_KC_BASE = config.OIDC_ISSUER.rsplit("/realms/", 1)[0]  # .../  (sin /realms/x)
_REALM = config.OIDC_ISSUER.rsplit("/realms/", 1)[1]    # 'fabrica'

# Roles del realm que NO se exponen como asignables (técnicos de Keycloak).
_HIDDEN_ROLES = {"default-roles-fabrica", "uma_authorization", "offline_access"}


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.post(
        f"{_KC_BASE}/realms/master/protocol/openid-connect/token",
        data={"client_id": "admin-cli", "grant_type": "password",
              "username": config.KC_ADMIN_USER,
              "password": config.KC_ADMIN_PASSWORD},
    )
    r.raise_for_status()
    return r.json()["access_token"]


class KCAdmin:
    """Context manager con token de admin ya obtenido."""

    def __init__(self) -> None:
        self._c = httpx.AsyncClient(timeout=30.0)
        self._tok = ""

    async def __aenter__(self) -> "KCAdmin":
        self._tok = await _token(self._c)
        self._c.headers["Authorization"] = f"Bearer {self._tok}"
        return self

    async def __aexit__(self, *_e) -> None:
        await self._c.aclose()

    def _u(self, path: str) -> str:
        return f"{_KC_BASE}/admin/realms/{_REALM}{path}"

    async def list_realm_roles(self) -> list[str]:
        r = await self._c.get(self._u("/roles"))
        r.raise_for_status()
        return sorted(role["name"] for role in r.json()
                      if role["name"] not in _HIDDEN_ROLES)

    async def list_users_with_role(self, role: str) -> list[dict]:
        """Usuarios con el rol del realm: [{username, name}]. Una llamada por rol.
        Para los picklists del Gantt filtrados por rol."""
        if not role:
            return []
        r = await self._c.get(self._u(f"/roles/{role}/users"), params={"max": 500})
        if r.status_code != 200:
            return []
        return [{"username": u.get("username"),
                 "name": f"{u.get('firstName','')} {u.get('lastName','')}".strip()
                         or u.get("username")}
                for u in r.json()]

    async def list_users_basic(self) -> list[dict]:
        """Usuarios del realm con su nombre para mostrar: [{username, name}].
        Ligero (una llamada, sin roles) — para mapear username→Nombre Apellido."""
        r = await self._c.get(self._u("/users"),
                              params={"max": 500, "briefRepresentation": "true"})
        r.raise_for_status()
        return [{"username": u.get("username"),
                 "name": f"{u.get('firstName','')} {u.get('lastName','')}".strip()
                         or u.get("username")}
                for u in r.json()]

    async def list_users(self) -> list[dict]:
        # briefRepresentation=false → KC incluye `attributes` (necesario para
        # leer `proyectos`); el listado breve por defecto los omite.
        r = await self._c.get(self._u("/users"),
                              params={"max": 200, "briefRepresentation": "false"})
        r.raise_for_status()
        out = []
        for u in r.json():
            roles = await self._user_role_names(u["id"])
            out.append({
                "id": u["id"], "username": u.get("username"),
                "email": u.get("email"),
                "name": f"{u.get('firstName','')} {u.get('lastName','')}".strip(),
                "enabled": u.get("enabled", True),
                "roles": roles,
                # Proyectos OP asignados (segmentación). Atributo multivaluado
                # `proyectos` = lista de IDs de proyecto OpenProject (strings).
                "proyectos": (u.get("attributes") or {}).get("proyectos") or [],
                # Capacidades OP asignadas (segmentación fina dentro del proyecto).
                # Lista de IDs de WP tipo Capability (strings).
                "capacidades": (u.get("attributes") or {}).get("capacidades") or [],
            })
        return out

    async def _user_role_names(self, user_id: str) -> list[str]:
        r = await self._c.get(self._u(f"/users/{user_id}/role-mappings/realm"))
        if r.status_code != 200:
            return []
        return sorted(role["name"] for role in r.json()
                      if role["name"] not in _HIDDEN_ROLES)

    async def _role_reps(self, names: list[str]) -> list[dict]:
        """Representaciones {id,name} de roles del realm por nombre."""
        r = await self._c.get(self._u("/roles"))
        r.raise_for_status()
        by_name = {role["name"]: role for role in r.json()}
        return [{"id": by_name[n]["id"], "name": n} for n in names if n in by_name]

    async def create_user(self, username: str, email: str, first: str,
                          last: str, password: str, roles: list[str]) -> dict:
        body = {
            "username": username, "email": email, "enabled": True,
            "emailVerified": True, "firstName": first, "lastName": last,
            "credentials": [{"type": "password", "value": password,
                             "temporary": True}],
        }
        r = await self._c.post(self._u("/users"), json=body)
        if r.status_code not in (201, 204):
            raise RuntimeError(f"KC crear usuario -> {r.status_code}: {r.text[:200]}")
        # Recuperar el id (Keycloak lo devuelve en Location).
        loc = r.headers.get("Location", "")
        user_id = loc.rstrip("/").rsplit("/", 1)[-1] if loc else ""
        if not user_id:
            q = await self._c.get(self._u("/users"), params={"username": username, "exact": "true"})
            els = q.json() if q.status_code == 200 else []
            user_id = els[0]["id"] if els else ""
        if roles and user_id:
            await self.set_roles(user_id, roles)
        return {"id": user_id, "username": username}

    async def set_roles(self, user_id: str, roles: list[str]) -> None:
        """Reemplaza los roles del realm del usuario por `roles` (lista de
        nombres). Quita los que sobran, agrega los que faltan."""
        current = await self._user_role_names(user_id)
        to_add = [r for r in roles if r not in current]
        to_del = [r for r in current if r not in roles]
        if to_add:
            reps = await self._role_reps(to_add)
            if reps:
                await self._c.post(
                    self._u(f"/users/{user_id}/role-mappings/realm"), json=reps)
        if to_del:
            reps = await self._role_reps(to_del)
            if reps:
                await self._c.request(
                    "DELETE",
                    self._u(f"/users/{user_id}/role-mappings/realm"), json=reps)

    async def set_user_projects(self, user_id: str,
                                project_ids: list[str]) -> None:
        """Reemplaza el atributo `proyectos` del usuario (segmentación por
        proyecto OpenProject). GET la representación completa, actualiza solo
        el atributo y PUT (KC reemplaza `attributes` con lo enviado)."""
        r = await self._c.get(self._u(f"/users/{user_id}"))
        r.raise_for_status()
        rep = r.json()
        attrs = dict(rep.get("attributes") or {})
        # Normalizamos a lista de strings sin vacíos ni duplicados.
        clean = sorted({str(p).strip() for p in project_ids if str(p).strip()})
        if clean:
            attrs["proyectos"] = clean
        else:
            attrs.pop("proyectos", None)
        rep["attributes"] = attrs
        pr = await self._c.put(self._u(f"/users/{user_id}"), json=rep)
        if pr.status_code not in (200, 204):
            raise RuntimeError(f"KC set proyectos -> {pr.status_code}: {pr.text[:200]}")

    async def set_user_capabilities(self, user_id: str,
                                    cap_ids: list[str]) -> None:
        """Reemplaza el atributo `capacidades` (IDs de WP Capability)."""
        r = await self._c.get(self._u(f"/users/{user_id}"))
        r.raise_for_status()
        rep = r.json()
        attrs = dict(rep.get("attributes") or {})
        clean = sorted({str(c).strip() for c in cap_ids if str(c).strip()})
        if clean:
            attrs["capacidades"] = clean
        else:
            attrs.pop("capacidades", None)
        rep["attributes"] = attrs
        pr = await self._c.put(self._u(f"/users/{user_id}"), json=rep)
        if pr.status_code not in (200, 204):
            raise RuntimeError(f"KC set capacidades -> {pr.status_code}: {pr.text[:200]}")

    async def get_user(self, user_id: str) -> dict:
        r = await self._c.get(self._u(f"/users/{user_id}"))
        r.raise_for_status()
        return r.json()

    async def set_enabled(self, user_id: str, enabled: bool) -> None:
        r = await self._c.put(self._u(f"/users/{user_id}"),
                              json={"enabled": enabled})
        if r.status_code not in (200, 204):
            raise RuntimeError(f"KC enabled -> {r.status_code}: {r.text[:200]}")

    async def set_password(self, user_id: str, password: str,
                           temporary: bool = True) -> None:
        """Fija la contraseña del usuario (acción de administrador). Si
        `temporary` es True, Keycloak fuerza el cambio en el próximo login
        (required action UPDATE_PASSWORD). Endpoint: PUT reset-password."""
        r = await self._c.put(
            self._u(f"/users/{user_id}/reset-password"),
            json={"type": "password", "value": password,
                  "temporary": temporary},
        )
        if r.status_code not in (200, 204):
            raise RuntimeError(
                f"KC reset-password -> {r.status_code}: {r.text[:200]}")
