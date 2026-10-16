"""Config del Panel de Control."""
from __future__ import annotations
import os

# Hostname / branding
PANEL_BASE_URL = os.environ.get("PANEL_BASE_URL", "http://localhost:8001")
PANEL_TITLE = os.environ.get("PANEL_TITLE", "Panel de Control · Fábrica")

# --- Auth (Keycloak OIDC) ---
OIDC_ISSUER = os.environ.get(
    "OIDC_ISSUER", "http://kc.fabrica.local:8090/realms/fabrica")
OIDC_PUBLIC_ISSUER = os.environ.get(
    "OIDC_PUBLIC_ISSUER", "http://kc.fabrica.local:8090/realms/fabrica")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "panel")
OIDC_CLIENT_SECRET = os.environ.get(
    "OIDC_CLIENT_SECRET", "panel-secret-local-2026")

SESSION_SECRET = os.environ.get(
    "SESSION_SECRET",
    "panel-dev-only-session-secret-do-not-use-in-prod-32b")

# --- Mapa de acceso por sistema (CONFIGURABLE por env) --------------------
# Define qué roles entran a cada sistema. El Panel lo muestra por usuario
# (columna "Accesos": quién entra a qué). Cambiar = solo editar el env.
#
# Panel de Control: PMs (de fábrica y de proyecto) + admin (para administrar
# usuarios). El resto NO entra al panel; trabajan por el Inbox.
ALLOWED_ROLES = set(
    (os.environ.get("PANEL_ALLOWED_ROLES")
     or "admin,swf-project-manager,project-manager").split(","))
# Inbox de la fábrica: todos los roles operativos (revisión, VoBo, dev, QA) +
# PMs + admin.
INBOX_ROLES = set(
    (os.environ.get("INBOX_ROLES")
     or "admin,swf-funcional,funcional,usuario-clave,swf-desarrollador,"
        "swf-tester,swf-project-manager,project-manager").split(","))

# Mapa {sistema: roles} para mostrar/consumir "quién entra a qué".
ACCESS_MAP = {
    "Panel": ALLOWED_ROLES,
    "Inbox": INBOX_ROLES,
}

# --- Integraciones (futuras) ---
GITEA_BASE_URL = os.environ.get("GITEA_BASE_URL", "http://gitea:3000")
GITEA_API_TOKEN = os.environ.get("GITEA_API_TOKEN", "")
# Token de SITE-ADMIN de Gitea (opcional). El token de fábrica administra las
# orgs que crea (teams/membresía) pero NO crea usuarios (/admin/users = 403). Si
# se define, el provisioning por usuario también CREA la cuenta Gitea.
GITEA_ADMIN_TOKEN = os.environ.get("GITEA_ADMIN_TOKEN", "")
GITEA_ARTIFACTS_OWNER = os.environ.get("FACTORY_GITEA_OWNER", "mcp-agent")
GITEA_ARTIFACTS_REPO = os.environ.get("FACTORY_GITEA_REPO", "fabrica")

# URL pública del Inbox (para deeplink desde el Gantt: {INBOX}/inbox#wp=N).
INBOX_BASE_URL = os.environ.get("INBOX_BASE_URL", "").rstrip("/")

# Para gestionar usuarios — KC admin API
KC_ADMIN_USER = os.environ.get("KC_ADMIN_USER", "admin")
KC_ADMIN_PASSWORD = os.environ.get("KC_ADMIN_PASSWORD", "Admin-Local-2026!")
