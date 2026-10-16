# Bridge abapGit ↔ MCP — `ZCL_7BS_ABAPGIT_API`

Respaldo del **handler HTTP** que el MCP ABAP usa para operar SAP por la **capa de objeto
de abapGit** (crear/serializar/mover/borrar objetos, restore, y mantenimiento SM30 headless).

> ⚠️ Es una **herramienta DEV, NO se transporta**. Vive en el paquete **local `$7BSADTGIT`**
> de cada DEV de cliente. Este archivo es el **backup del fuente** (no está en SAP bajo git;
> si refrescan el DEV, se reinstala desde aquí). Lo que SÍ se transporta a Calidad son los
> entregables (paquete Z + OT), nunca el bridge.

## Instalación (1 vez por DEV de cliente, manual)

1. Crear el paquete local **`$7BSADTGIT`** (SE21/SE80, sin OT).
2. Crear la clase **`ZCL_7BS_ABAPGIT_API`** con el fuente de
   [`ZCL_7BS_ABAPGIT_API.clas.abap`](./ZCL_7BS_ABAPGIT_API.clas.abap) y activarla.
   (Requiere la **developer version de abapGit** instalada — clases `ZCL_ABAPGIT_*` reales.
   Ver memoria `abapgit-dev-wiring-mcp`.)
3. Crear el nodo SICF **`/sap/bc/z7bs/abapgit`** (SICF), handler class = `ZCL_7BS_ABAPGIT_API`,
   y activarlo. Logon por usuario técnico (basic auth).
4. Publicar por Caddy con basic_auth (reverse proxy del EIP). El MCP llama
   `call_custom_service(path, json)` (`sap_client.py`) que hace POST a
   `/sap/bc/z7bs/abapgit{path}` reusando sesión/auth/CSRF de ADT.

## Endpoints (path → método)

| Path | Qué hace |
|---|---|
| `/serializar` | objeto SAP → archivos abapGit |
| `/crear_objeto` | archivos abapGit → objeto (deserializa+activa), `package`+`transport` por objeto |
| `/restaurar` | varios objetos (ordenados por tier) en un paso |
| `/borrar_objeto` | `zif_abapgit_object~delete` + COMMIT |
| `/mover_paquete` | reasigna paquete (TADIR `insert_single`) — ADT no puede |
| `/mover_ot` | inserta objeto en OT (no reubica locks ya existentes) |
| `/textos` | text pool de un PROG (INSERT TEXTPOOL + activación REPT) |
| `/campos_tabla` | campos de una tabla (DDIF_FIELDINFO_GET) — insumo SM30 |
| `/registrar_sm30` | registra mantenimiento SM30 (MODIFY TVDIR) — reversible |
| `/desregistrar_sm30` | quita registro SM30 (DELETE TVDIR/TVIMF) — antes de borrar la tabla |
| `/check_sm30` | diagnóstico: lee TVDIR/TVIMF de una tabla |
| `/generar_mantenimiento` | fallback GUI (`TABLE_MAINTENANCE_GENERATOR`) — NO funciona headless |

## Mantenimiento SM30 headless

SE54 es GUI-only. El flujo headless (tool MCP `aws_abapgit_crear_mantenimiento`) es:
`/campos_tabla` → templatiza el FUGR de mantenimiento (`maint_fugr.py` + `maint_funcs.xml`
en `aws-7bs-abap-mcp/src/aws_abap_accelerator/`) → `/crear_objeto` (deserializa+activa) →
`/registrar_sm30` (escribe TVDIR). El inverso es `aws_abapgit_borrar_mantenimiento`.
