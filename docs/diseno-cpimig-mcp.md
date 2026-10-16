# Diseño — `cpimig-mcp`: MCP genérico para migración SAP CPI (Neo → Cloud Foundry)

> Estado: **diseño aprobado, pendiente construcción**. Fecha: 2026-07-21.
> Insumo base: `docs/ci.migrationpack-1.9.1.zip` (SAP CPI Migration Assistant v1.9.1,
> 13 colecciones Postman). El pack es la **especificación**: cada colección MIGxxx
> mapea 1:1 a un dominio de migración y se vuelve un checklist verificable.

## 1. Objetivo

Acelerar la migración de tenants SAP Cloud Integration de **Neo → Cloud Foundry**
convirtiendo los ~50 requests manuales del pack Postman en un **MCP genérico**
(multi-cliente) que Claude Code / Codex orquesta, itera y verifica solo, más una
**vista tipo inbox** en el panel que muestra los flujos de origen y destino lado a
lado como foto de avance.

Genérico = las **conexiones** (pares origen/destino con sus credenciales) se crean y
administran desde el panel; ninguna credencial ni host va hardcodeado. Sirve para
cualquier cliente.

## 2. Qué hace el pack Postman (análisis)

Todas las colecciones son el mismo patrón sobre la **API OData pública de CPI**
(`/api/v1/`): **GET del tenant origen → transformar → POST al tenant destino**.

| # | Dominio | Mecanismo | Escribe |
|---|---------|-----------|:-------:|
| 010 | Connect to Tenants | OAuth2 client_credentials + XSRF (`X-CSRF-Token: Fetch`) | — |
| 020 | Readiness Checks | Inventario de paquetes + versiones de iFlows/ValMaps + JMS | — |
| 030 | Certificates → Service Key | CF Cloud Controller v3 + API plataforma Neo (crea service instance/key) | ✔ |
| 040 | Data Stores | **Helper flow**: iFlow temporal para extraer/inyectar datos de runtime | ✔ |
| 050 | Variables | Helper flow temporal | ✔ |
| 060 | Custom Tags | GET `CustomTagConfigurations/$value` → POST `?Overwrite=true` | ✔ |
| 070 | Number Range Objects | GET lista → download c/u → POST | ✔ |
| 080 | Access Policies | GET policy + ArtifactReferences → POST | ✔ |
| 090 | Security Artifacts | `SecurityContentTransports` (keystore, pgp, jdbc, oauth, knownHosts) | ✔ |
| 100 | Pre-Packaged Content | `CopyIntegrationPackage` + configs vía `$batch` | ✔ |
| 110 | Custom Content | Download package `$value` → upload + configs (loop artefactos) | ✔ |
| 120 | Value Mapping Values | Varios `$batch` (schema + default + values) | ✔ |

### Modelo de autenticación (el corazón que el MCP replica)
- **OAuth2 client_credentials** en origen y destino → `Authorization: Bearer <token>`.
  - Origen Neo: `POST https://<tokenHost>/oauth2/api/v1/token`
  - Destino CF: `POST https://<tokenHost>/oauth/token`
- **XSRF/CSRF** para toda escritura: `GET .../api/v1/` con header `X-CSRF-Token: Fetch`
  → se guarda el token de la respuesta y se reenvía en POST/DELETE.
- MIG030 usa además: UAA CF (`uaa.cf.<domain>/oauth/token`), Cloud Controller
  (`api.cf.<domain>/v3/...`) y plataforma Neo (`api.<domain>/oauth2/apitoken/v1`).

### Variables del environment (lo que hoy se llena a mano)
`sourceHost/targetHost`, `sourceToken/targetToken`, `srcXSRFToken/tgtXSRFToken`,
`*OauthId/*OauthSecret`, `packageTechnicalName`, `artifactId`, `flowId`, `varName`,
`datastoreName`, `tgtDomain/srcDomain`, `spaceName`, `cfOrgId`, etc. → **el MCP las
resuelve o itera solo**.

## 3. Arquitectura (calca abap-mcp / sapcfg-mcp)

```
Claude Code / Codex (laptop)
   │  HTTPS  mcp.<dominio>/cpimig/mcp   (basic_auth en Caddy)
   ▼
Caddy  ──handle_path /cpimig/*──▶  cpimig-mcp:8000  (FastMCP, /mcp)
                                        │
                        ┌───────────────┼───────────────┐
                        ▼               ▼               ▼
                 proyecto-db      SSM SecureString   Tenants CPI
             (cpi_connection      /fabrica-sw/cpi/  (origen Neo +
              espejo no-secreto)   <connection_id>   destino CF) vía OData
```

- **Contenedor** `infra/app/cpimig-mcp/`: `server.py` (FastMCP) + `Dockerfile` +
  `requirements.txt` (`fastmcp`, `httpx`, `boto3`, `asyncpg`). Escucha `:8000` en `/mcp`.
- **docker-compose.yml**: servicio `cpimig-mcp` (build `./cpimig-mcp`), con
  `PROYECTO_DB_DSN`, `SSM_CPI_PREFIX=/fabrica-sw/cpi`, `AWS_REGION`.
- **Caddyfile** bajo `mcp.{$BASE_DOMAIN}`:
  ```
  handle_path /cpimig/* {
      basic_auth { abap <hash> }
      reverse_proxy cpimig-mcp:8000
  }
  ```
  Recordatorio: editar el Caddyfile en AWS **no** aplica solo → reiniciar
  `fabrica-aws-caddy-1` (ver memoria caddy-reload-tras-editar).

### El corazón: `CpiClient`
Una sola clase encapsula lo tedioso; los 13 dominios son orquestaciones delgadas encima:
- `get_token()` — OAuth2 client_credentials, cacheado + refresh por expiración.
- `fetch_xsrf()` — obtiene y cachea el CSRF para escrituras.
- `get(path, paginado)` — GET OData con paginación `$skip/$top` automática.
- `post/delete(path, ..., xsrf)` — escrituras con el CSRF puesto.
- `batch(parts)` — helper `$batch` multipart.
- `helper_flow(...)` — ciclo crear → configurar → desplegar → borrar (Data Stores/Variables).

## 4. Conexiones genéricas (registro desde el panel)

**Decisión:** calcar el patrón ya probado de `sap_system` (abap-mcp):
- Espejo **no-secreto** en proyecto-db, tabla nueva `cpi_connection`:
  `id, cliente_id, nombre, source_kind (neo|cf), source_host, source_token_host,
   source_oauth_id, target_host, target_token_host, target_oauth_id, tgt_domain,
   cf_org_id, space_name, description, ssm_param, created/updated_at`.
- **Secretos** (`*_oauth_secret`, tokens de plataforma) en **SSM SecureString**
  `/fabrica-sw/cpi/<connection_id>` (bundle JSON), como hace `crear_sap_system`.
- El panel reutiliza los helpers existentes `_ssm_put/_ssm_get/_ssm_delete` de
  `proyecto_db.py`. El MCP resuelve la conexión con un `_resolve_connection(id)`
  calcado de `_resolve_system` del sapcfg (lee espejo BD + secreto SSM, cacheado).
- Alternativa si se prefiere autocontenido: secretos cifrados con `pgcrypto` en la
  misma tabla. (Recomendado: SSM, por reuso de plomería.)

## 5. Superficie de tools del MCP

Cada tool recibe `connection_id`. Las de escritura son **idempotentes con `dry_run`
+ gate humano** (mismo esquema preview/apply del sapcfg y la cultura de "confirmar
antes de escribir").

```
# Lectura / diagnóstico (riesgo cero) — alimentan el inbox del panel
cpi_status(connection_id)                 # valida ambos tenants: tokens, XSRF, alcance
cpi_readiness(connection_id)              # MIG020: inventario + versiones + JMS
cpi_list_flows(connection_id, side, pkg?) # iFlows/paquetes de un lado (source|target)
cpi_diff(connection_id, domain?)          # origen vs destino: solo-origen / en-ambos / versión distinta

# Migración por dominio (dry_run por defecto; escribe solo con confirmación)
cpi_migrate_prepackaged(connection_id, package?, dry_run=True)      # MIG100
cpi_migrate_custom_content(connection_id, package?, dry_run=True)   # MIG110
cpi_migrate_datastores(connection_id, package?, dry_run=True)       # MIG040
cpi_migrate_variables(connection_id, package?, dry_run=True)        # MIG050
cpi_migrate_value_mappings(connection_id, artifact?, dry_run=True)  # MIG120
cpi_migrate_number_ranges(connection_id, dry_run=True)             # MIG070
cpi_migrate_custom_tags(connection_id, dry_run=True)              # MIG060
cpi_migrate_access_policies(connection_id, dry_run=True)          # MIG080
cpi_migrate_security_artifacts(connection_id, dry_run=True)       # MIG090
cpi_migrate_certificates(connection_id, dry_run=True)            # MIG030 (CF v3 + Neo)
```

## 6. Panel / inbox (app `panel/app`)

Nueva sección **"Migración CPI"**:
1. **Conexiones**: CRUD de pares origen/destino + botón *Probar* (invoca `cpi_status`).
   Reusa el patrón de alta de sistemas SAP (`crear_sap_system`).
2. **Inbox origen↔destino**: tabla lado a lado **Origen | Destino** de paquetes/iFlows
   con estado por fila (`solo-origen` / `en-ambos` / `versión distinta`), alimentada
   por `cpi_list_flows` / `cpi_diff`. Es la foto de avance de la migración.
3. (Futuro) botón por paquete para disparar migración; por ahora lectura en el panel
   y las escrituras las hace Claude Code vía las tools con gate.

## 7. Fases de construcción

- **F0 — Scaffold del contenedor.** `CpiClient` (token+XSRF+GET paginado) +
  `cpi_status` + `cpi_list_flows`; servicio en compose + ruta Caddy; desplegar por git.
  Prueba: `cpi_status` contra un tenant real. *(riesgo cero, solo lectura)*
- **F1 — Diagnóstico completo.** `cpi_readiness` + `cpi_diff` (los 13 dominios en modo
  inventario/comparación). Solo lectura.
- **F2 — Panel.** Sección Conexiones (CRUD + Probar) + vista inbox origen/destino.
- **F3 — Migración por dominio.** Tools `migrate_*` con `dry_run` + gate, una por una,
  empezando por Custom Content (MIG110) como vertical slice completa.
- **F4 — Reporte + memoria.** Reporte de migración por conexión + memoria por conexión
  (handoff entre consultores, como `guardar_memoria` de la fábrica).

## 8. Notas / convenciones a respetar

- Desplegar SIEMPRE por git (commit → push → pull+rebuild en AWS), nunca copiar código
  directo a AWS (memoria flujo-trabajo-sin-commits).
- Trabajar local, NO commitear salvo que se pida.
- SAP/tenants vivos: cero suposiciones, verificar en vivo antes de afirmar/actuar.
- Editar Caddyfile → reiniciar `fabrica-aws-caddy-1`.
- QAS/producción de clientes: si aplica, solo lectura salvo autorización explícita.
