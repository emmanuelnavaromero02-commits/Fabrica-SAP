"""Configuracion del orquestador, leida del entorno.

Apunta al despliegue local por defecto. Para AWS se cambian las URLs.
"""
from __future__ import annotations

import os

# --- Temporal ---
TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "default")
TASK_QUEUE = os.environ.get("FACTORY_TASK_QUEUE", "fabrica")
# URL de Temporal Web que ve el BROWSER (link "ver workflow" del Inbox).
# En AWS: https://temporal.<dominio>. Si está vacía, el Inbox oculta el link.
TEMPORAL_WEB_URL = os.environ.get("TEMPORAL_WEB_URL", "").rstrip("/")

# --- Gitea (API v1) ---
GITEA_BASE_URL = os.environ.get("GITEA_BASE_URL", "").rstrip("/")
GITEA_API_TOKEN = os.environ.get("GITEA_API_TOKEN", "")
# URL de Gitea que ve el BROWSER (links "ver en Gitea" del Inbox). En AWS
# GITEA_BASE_URL ya es pública (https://git.<dominio>) → default sirve. En
# local, si GITEA_BASE_URL apunta a un nombre interno (gitea:3000), setear
# GITEA_PUBLIC_URL al puerto publicado (http://localhost:3000). Mismo patrón
# que OIDC_ISSUER vs OIDC_PUBLIC_ISSUER.
GITEA_PUBLIC_URL = os.environ.get("GITEA_PUBLIC_URL", GITEA_BASE_URL).rstrip("/")
# Repositorio de la fabrica donde se versionan los artefactos.
FACTORY_GITEA_OWNER = os.environ.get("FACTORY_GITEA_OWNER", "")
FACTORY_GITEA_REPO = os.environ.get("FACTORY_GITEA_REPO", "")

# --- Agente de IA ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
FACTORY_MODEL = os.environ.get("FACTORY_MODEL", "claude-sonnet-4-6")

# --- Arquitectura del proyecto (cacheada en el prompt del agente) ---
# Archivo Markdown en un repo de Gitea. El agente lo carga en cada run y lo
# pone en el system prompt con cache_control 'ephemeral'. Si esta vacio, el
# agente corre sin contexto arquitectonico.
FACTORY_ARCH_REPO = os.environ.get("FACTORY_ARCH_REPO", "")
FACTORY_ARCH_PATH = os.environ.get("FACTORY_ARCH_PATH", "arquitectura.md")
FACTORY_ARCH_BRANCH = os.environ.get("FACTORY_ARCH_BRANCH", "main")
# Plantilla canónica para spec.md (bloque 'diseno'). Vive en el mismo repo
# que la arquitectura. Si no existe, el agente improvisa estructura.
FACTORY_SPEC_TEMPLATE_PATH = os.environ.get(
    "FACTORY_SPEC_TEMPLATE_PATH", "spec_template.md")
# Skill del proyecto para el bloque 'desarrollo' (cómo generar el código del
# stack: abapclasic/abapcloud/fiori/cap, etc.). Vive en el repo de
# arquitectura. Si está vacío o no existe, el agente dev usa solo la
# arquitectura + spec + assertions. Por proyecto se apunta a su .skill.
FACTORY_SKILL_PATH = os.environ.get("FACTORY_SKILL_PATH", "skill.md")
# Catalogo de skills seleccionables (clave -> bundle .skill). Si existe, el
# agente de desarrollo usa el skill que el humano elige al liberar desarrollo;
# si no, cae al FACTORY_SKILL_PATH unico (compat). Ver skills/catalog.yaml.
FACTORY_SKILLS_CATALOG_PATH = os.environ.get(
    "FACTORY_SKILLS_CATALOG_PATH", "skills/catalog.yaml")

# URL del MCP `fabrica` (hosted) que se SIEMBRA en el .mcp.json del repo del
# requerimiento, para que Claude Code/Codex local lo consuma. Por ambiente:
# local p.ej. http://localhost:8080/mcp ; AWS https://mcp.<BASE_DOMAIN>/mcp.
# Vacío = no sembrar .mcp.json (el repo se entrega sin wiring del MCP).
FABRICA_MCP_URL = os.environ.get("FABRICA_MCP_URL", "")

# URL del MCP ABAP (SAP ADT) que se SIEMBRA en el .mcp.json de los repos ABAP,
# para que Claude Code cree objetos directo en SAP por el túnel VPN. AWS:
# https://mcp.<BASE_DOMAIN>/abap/mcp. Vacío = no sembrar el server `abap`. El
# endpoint está protegido por basic_auth en Caddy; el dev expande ${ABAP_MCP_BASIC}
# de su entorno. El sistema concreto va por header x-sap-system-id (del cliente).
ABAP_MCP_URL = os.environ.get("ABAP_MCP_URL", "")

# --- S3 storage (MinIO local, S3 en AWS) ---
# El browser sube DIRECTO al endpoint público con presigned URL.
# El backend (webhook/worker) usa el endpoint interno para descarga.
# En AWS, ambos pueden ser el mismo (https://s3.region.amazonaws.com).
S3_ENDPOINT_PUBLIC = os.environ.get(
    "S3_ENDPOINT_PUBLIC", "http://localhost:9000")
S3_ENDPOINT_INTERNAL = os.environ.get(
    "S3_ENDPOINT_INTERNAL", "http://minio:9000")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "admin")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "minio-local-2026-changeme")
S3_BUCKET = os.environ.get("S3_BUCKET", "fabro-attachments")
S3_PRESIGNED_TTL = int(os.environ.get("S3_PRESIGNED_TTL", "3600"))  # 1h

# --- Auth / IdP (Keycloak OIDC) ---
# Si OIDC_ISSUER está vacío, el inbox corre sin auth (modo dev legado).
# Usamos UN solo hostname (kc.fabrica.local:8090) tanto desde el browser
# como desde los containers (via extra_hosts: kc.fabrica.local→host-gateway).
# Eso permite SSO real (cookies de KC con un dominio) y evita splits frágiles.
OIDC_ISSUER = os.environ.get(
    "OIDC_ISSUER", "http://kc.fabrica.local:8090/realms/fabrica")
OIDC_DISCOVERY_URL = f"{OIDC_ISSUER}/.well-known/openid-configuration"
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "inbox")
OIDC_CLIENT_SECRET = os.environ.get(
    "OIDC_CLIENT_SECRET", "inbox-secret-local-2026")
# Issuer público (lo que el browser ve). Igual al interno gracias al
# hostname único + hosts file edit.
OIDC_PUBLIC_ISSUER = os.environ.get(
    "OIDC_PUBLIC_ISSUER", "http://kc.fabrica.local:8090/realms/fabrica")
INBOX_BASE_URL = os.environ.get("INBOX_BASE_URL", "http://localhost:8000")
# Clave para firmar la cookie de sesión. CAMBIAR en prod.
SESSION_SECRET = os.environ.get(
    "SESSION_SECRET",
    "dev-only-session-secret-do-not-use-in-prod-32bytes-min-")

# KC Admin API — el Inbox lee el atributo `proyectos` del usuario para
# segmentar qué requisitos ve (fuente de verdad la escribe el Panel). Mismas
# credenciales que el Panel; van por .env (compartido con el webhook).
KC_ADMIN_USER = os.environ.get("KC_ADMIN_USER", "admin")
KC_ADMIN_PASSWORD = os.environ.get("KC_ADMIN_PASSWORD", "Admin-Local-2026!")

# --- Pipeline declarativo (YAML interpretado por el workflow) ---
# Archivo en el MISMO repo de la arquitectura, por convencion. Editar el
# YAML + PR == cambiar el pipeline sin tocar codigo. Si no se define,
# el worker usa un pipeline embebido equivalente al v1.
FACTORY_PIPELINE_REPO = os.environ.get("FACTORY_PIPELINE_REPO", FACTORY_ARCH_REPO)
FACTORY_PIPELINE_PATH = os.environ.get("FACTORY_PIPELINE_PATH", "pipeline.yaml")
FACTORY_PIPELINE_BRANCH = os.environ.get("FACTORY_PIPELINE_BRANCH", FACTORY_ARCH_BRANCH)
