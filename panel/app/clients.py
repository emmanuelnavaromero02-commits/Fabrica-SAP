"""Cliente mínimo del Panel: Gitea (timeline.json por WP + provisioning de
acceso). El estado de proyecto/requisitos vive en proyecto-db (ver proyecto_db);
OpenProject quedó fuera."""
from __future__ import annotations

import base64
import json
import re
import unicodedata

import httpx

import config


def slug_org(name: str, max_len: int = 40) -> str:
    """Slug del nombre de ORG de Gitea (= cliente). IGUAL que orquestador
    clients.slug_org para que el grant apunte a la MISMA org que crea
    provisionar_repo. Ej: 'TAILOY' -> 'tailoy'."""
    norm = unicodedata.normalize("NFKD", name or "")
    ascii_txt = "".join(c for c in norm if not unicodedata.combining(c)).lower()
    tokens = re.findall(r"[a-z0-9]+", ascii_txt)
    slug = "-".join(tokens)[:max_len].strip("-")
    return slug or "sin-cliente"


class Gitea:
    def __init__(self) -> None:
        self._c = httpx.AsyncClient(
            base_url=f"{config.GITEA_BASE_URL}/api/v1",
            headers={"Authorization": f"token {config.GITEA_API_TOKEN}",
                     "Accept": "application/json"},
            timeout=30.0,
        )

    async def __aenter__(self) -> "Gitea":
        return self

    async def __aexit__(self, *_e) -> None:
        await self._c.aclose()

    async def read_timeline(self, wp_id: int) -> list[dict]:
        """timeline.json de la rama req-N: [{stage, etapa, etapa_key, ts}].
        Vacío si no existe."""
        r = await self._c.get(
            f"/repos/{config.GITEA_ARTIFACTS_OWNER}/"
            f"{config.GITEA_ARTIFACTS_REPO}/contents/timeline.json",
            params={"ref": f"req-{wp_id}"})
        if r.status_code != 200:
            return []
        try:
            data = json.loads(base64.b64decode(r.json()["content"]).decode("utf-8"))
            return data.get("timeline") or []
        except Exception:  # noqa: BLE001
            return []

    async def read_asignacion(self, wp_id: int) -> dict:
        """asignaciones.json de la rama req-N: {funcional, usuario_clave, ...}.
        {} si no existe. Lo escribe el inbox al asignar consultor/usuario clave."""
        r = await self._c.get(
            f"/repos/{config.GITEA_ARTIFACTS_OWNER}/"
            f"{config.GITEA_ARTIFACTS_REPO}/contents/asignaciones.json",
            params={"ref": f"req-{wp_id}"})
        if r.status_code != 200:
            return {}
        try:
            return json.loads(
                base64.b64decode(r.json()["content"]).decode("utf-8")) or {}
        except Exception:  # noqa: BLE001
            return {}

    # --- Provisioning de acceso por usuario (admin) -----------------------
    async def user_exists(self, username: str) -> bool:
        r = await self._c.get(f"/users/{username}")
        return r.status_code == 200

    @staticmethod
    def _gitea_username_candidates(kc_username: str) -> list[str]:
        """Gitea NO admite '@' en el username: las cuentas creadas por SSO quedan
        con el preferred_username saneado (truncado en el '@'). Por eso el
        username de Keycloak (que suele ser el email) NO coincide con el de Gitea.
        Probamos el username tal cual y, si trae dominio, la parte local."""
        cands = [kc_username]
        if "@" in kc_username:
            local = kc_username.split("@", 1)[0]
            if local and local not in cands:
                cands.append(local)
        return cands

    async def find_user_by_email(self, email: str) -> str | None:
        """Busca la cuenta Gitea por email (las cuentas SSO lo traen). Best-effort:
        el search por email solo matchea si el email es visible para el token."""
        if not email:
            return None
        r = await self._c.get("/users/search", params={"q": email, "limit": 50})
        if r.status_code != 200:
            return None
        for u in (r.json().get("data") or []):
            if (u.get("email") or "").lower() == email.lower():
                return u.get("login") or u.get("username")
        return None

    async def resolve_user(self, kc_username: str, email: str) -> str | None:
        """username REAL en Gitea para un usuario de KC, o None si no existe."""
        for cand in self._gitea_username_candidates(kc_username):
            if await self.user_exists(cand):
                return cand
        return await self.find_user_by_email(email)

    async def ensure_user(self, username: str, email: str,
                          password: str) -> tuple[str, str]:
        """Garantiza la cuenta Gitea. Devuelve (estado, gitea_username):
          'exists'   ya existía. gitea_username = el nombre REAL en Gitea (puede
                     diferir del de KC por el saneo del '@' en el login SSO).
          'created'  se creó (mostrar el password una vez).
          'no_admin' no existe y el token NO es site-admin (403) → crear la
                     cuenta aparte (SSO o GITEA_ADMIN_TOKEN); la membresía igual
                     se intenta si la cuenta llega a existir."""
        resolved = await self.resolve_user(username, email)
        if resolved:
            return "exists", resolved
        # No existe en Gitea → crear (requiere site-admin). El username de Gitea
        # NO puede llevar '@': usamos la parte local.
        gitea_name = username.split("@", 1)[0] if "@" in username else username
        hdr = ({"Authorization": f"token {config.GITEA_ADMIN_TOKEN}"}
               if config.GITEA_ADMIN_TOKEN else None)
        r = await self._c.post("/admin/users", json={
            # email válido obligatorio en Gitea (regex con dominio.tld); si el
            # usuario no tiene email en Keycloak, placeholder con formato válido.
            "username": gitea_name, "email": email or f"{gitea_name}@noreply.local",
            "password": password, "must_change_password": False,
            "send_notify": False}, headers=hdr)
        if r.status_code == 201:
            return "created", gitea_name
        if r.status_code in (401, 403):
            return "no_admin", gitea_name
        body = (r.text or "").lower()
        if r.status_code in (409, 422) and ("already" in body or "exist" in body):
            return "exists", gitea_name
        # 422 = error de VALIDACIÓN (p.ej. email inválido) → surge el motivo.
        raise RuntimeError(f"Gitea crear usuario {gitea_name} -> "
                           f"HTTP {r.status_code}: {r.text[:200]}")

    async def ensure_team(self, org: str, name: str = "Colaboradores",
                          permission: str = "write") -> int | None:
        """Garantiza un team con `permission` sobre TODOS los repos de la org.
        Devuelve su id (o None si la org no existe)."""
        r = await self._c.get(f"/orgs/{org}/teams")
        if r.status_code == 404:
            return None
        if r.status_code == 200:
            for t in r.json():
                if (t.get("name") or "").lower() == name.lower():
                    return t.get("id")
        cr = await self._c.post(f"/orgs/{org}/teams", json={
            "name": name, "permission": permission,
            "includes_all_repositories": True,
            "units": ["repo.code", "repo.issues", "repo.pulls",
                      "repo.releases", "repo.wiki"],
            "can_create_org_repo": False})
        if cr.status_code not in (201, 422):
            raise RuntimeError(f"Gitea crear team en {org} -> "
                               f"HTTP {cr.status_code}: {cr.text[:200]}")
        if cr.status_code == 201:
            return cr.json().get("id")
        # 422: existía por carrera → re-leer
        r2 = await self._c.get(f"/orgs/{org}/teams")
        for t in (r2.json() if r2.status_code == 200 else []):
            if (t.get("name") or "").lower() == name.lower():
                return t.get("id")
        return None

    async def add_team_member(self, team_id: int, username: str) -> None:
        r = await self._c.put(f"/teams/{team_id}/members/{username}")
        if r.status_code not in (204, 200):
            raise RuntimeError(f"Gitea add member {username} team {team_id} -> "
                               f"HTTP {r.status_code}: {r.text[:200]}")
