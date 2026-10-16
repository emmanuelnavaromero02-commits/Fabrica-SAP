# Diseño — Wiring del MCP ABAP a los servicios de abapGit

> Estado: **diseño, sin código**. Decidido 2026-06-24.
> Objetivo: que el MCP ABAP (basado en ADT) pueda usar el **motor de abapGit**
> (clases `ZCL_ABAPGIT_*`, recién instaladas como developer version) para
> mover código entre SAP y el Gitea del requerimiento, sin ir objeto por objeto.

## 0. Decisión de unidad (2026-06-24)

**1 requerimiento = 1 paquete dedicado = 1 repo online abapGit = 1 repo Gitea.**
Todos los objetos que se tocan/crean en el requerimiento van a ese paquete.
→ Se usa el modelo **nativo** de abapGit (repo↔paquete 1:1); NO se usa filtrado
por lista de objetos (`zif_abapgit_object_filter`).

**Caveat:** el repo=paquete solo captura lo que **vive en ese paquete**. Sirve para
objetos **nuevos Z** (creados en el paquete del requerimiento). Objetos
**preexistentes que solo se modifican** (enhancements a estándar, objetos ya en
otro paquete del cliente) **no se reubican** y por tanto **no entran** al repo por
la vía paquete. Asumimos requerimientos mayormente Z-nuevo; si aparecen mods a
preexistentes, tratarlos aparte (transporte) — no por este puente.

## 1. El problema que resuelve

El MCP hoy solo hace CRUD de objetos por **ADT REST** (`SAPADTClient`):
crear/leer/activar, syntax, ATC, unit tests, transports, service bindings.
**No puede invocar lógica arbitraria** dentro de SAP, y ADT **no expone** un
endpoint genérico "llamá a este método de esta clase".

abapGit, en cambio, ya resuelve pull/push/serialize/deserialize de un repo
completo contra git. Pero su API (`zif_abapgit_repo_srv`, `zif_abapgit_repo`)
es **ABAP puro**: hay que invocarla *desde dentro* de SAP.

➡️ Necesitamos un **puente HTTP propio**: una clase Z que implemente
`IF_HTTP_EXTENSION`, registrada en SICF, que por dentro llame a la API de
abapGit y devuelva JSON. El MCP la consume por HTTP reusando su conexión actual.

## 2. Arquitectura

```
Claude Code / agente
        │  (MCP tools)
        ▼
MCP ABAP (Python, aiohttp)            infra/app/abap-mcp/.../sap/sap_client.py
   SAPADTClient  ── HTTPS basic/cert + CSRF ──┐
        │  nuevos tools: link/pull/status/push │
        ▼                                      ▼
SAP ICM  ──►  SICF  /sap/bc/zfabrica/abapgit   (servicio nuevo)
                         │
                         ▼
              ZCL_7BS_ABAPGIT_API  (IF_HTTP_EXTENSION)
                         │  llama API headless
                         ▼
              zcl_abapgit_repo_srv / zif_abapgit_repo  ──► git (Gitea)
```

Propiedad clave: el handler Z corre **bajo el mismo usuario SAP** con que el
MCP ya está autenticado (no hay auth nueva; reusa basic/cert + sesión).

## 3. La clase puente: `ZCL_7BS_ABAPGIT_API`

- Implementa `IF_HTTP_EXTENSION~HANDLE_REQUEST`.
- Enrutado por `path_info` + verbo: `/link`, `/pull`, `/status`, `/push`.
- Entrada/salida **JSON** (serialización con `/ui2/cl_json` o `zcl_abapgit_ajson`,
  que ya viene con abapGit).
- Toda operación corre headless: **sin popups**. Las decisiones que abapGit
  normalmente pregunta por GUI (overwrite, data loss, transporte) se resuelven
  programáticamente (ver §5).
- Errores: capturar `zcx_abapgit_exception`, devolver `{ ok:false, error, log[] }`
  con HTTP 422; éxito `{ ok:true, log[], ... }` con 200.

### SICF
- Nodo `/sap/bc/zfabrica/abapgit` (o bajo el árbol que ya use la fábrica).
- Handler = `ZCL_7BS_ABAPGIT_API`.
- Activar servicio; auth = estándar (hereda del logon del MCP).

## 4. Endpoints (contrato)

| Verbo/path        | Para qué | Entrada (JSON) | Salida (JSON) |
|-------------------|----------|----------------|----------------|
| `POST /link`      | Vincular repo↔paquete (crea repo online si no existe) | `url, branch, package, folder_logic?` | `repo_key, created` |
| `POST /pull`      | Deserializar remoto → SAP (con activación) | `repo_key` ó `url`, `git_user?, git_token?` | `log[], objects[]` |
| `GET  /status`    | Diff local vs remoto | `repo_key` ó `package` | `files[] {path, lstate, rstate}` |
| `POST /push`      | Serializar paquete → commit/push a git | `repo_key, commit{message,name,email}, git_user?, git_token?` | `sha1, log[]` |

> `git_user/git_token` opcionales: si no vienen, el handler los lee de un store
> en SAP (background user) o los pasa el MCP desde AWS Secrets (ver §6).

## 5. Mapeo a la API de abapGit (versión instalada, verificado por MCP)

`zif_abapgit_repo_srv` (vía `zcl_abapgit_repo_srv=>get_instance( )`):
- `new_online( iv_url, iv_branch_name, iv_package, iv_folder_logic )` → crea repo.
- `get_repo_from_url( )` / `get_repo_from_package( )` → buscar existente (idempotencia del `/link`).
- `get( iv_key )` → obtener `zif_abapgit_repo` por key.
- `purge( )`, `is_repo_installed( )`, `validate_package/url( )`.

### Flujo `/pull` (deserialize headless)
```
lo_srv  = zcl_abapgit_repo_srv=>get_instance( ).
li_repo = lo_srv->get_repo_from_url( iv_url )  " ó new_online(...) si no existe
         ó lo_srv->get( iv_key ).
" credenciales git ANTES de tocar el remoto (ver §6)
li_repo->refresh( ).
ls_checks = li_repo->deserialize_checks( ).
" resolver decisiones SIN popup:
"  - overwrite / warning_package / data_loss / delete_tabl_with_data → decision = c_yes
"  - transport: fijar trkorr si el sistema lo pide (ver §7)
li_log = li_repo->create_new_log( 'pull' ).
li_repo->deserialize( is_checks = ls_checks  ii_log = li_log ).
" devolver li_log->get_messages( ) como log[]
```

### Flujo `/status`
```
li_repo->refresh( ).
" diff local vs remoto: zcl_abapgit_repo_status=>calculate( li_repo )
" → ty_results_tt con lstate/rstate por archivo
```

### Flujo `/push`
- `push` **no** está en `zif_abapgit_repo`; vive en **`zif_abapgit_repo_online`**.
- Bosquejo (confirmar firma con `get_source ZIF_ABAPGIT_REPO_ONLINE` al implementar):
```
lo_online ?= li_repo.                  " cast a online
lt_status = zcl_abapgit_repo_status=>calculate( li_repo ).
lo_stage  = NEW zcl_abapgit_stage( ).
LOOP lt_status → lo_stage->add( ... ) / rm( ... ) según método.
DATA(ls_comment) = VALUE #( comment = ... committer-name = ... committer-email = ... ).
lo_online->push( is_comment = ls_comment  io_stage = lo_stage ).
```

> ⚠️ Pendiente al implementar push: confirmar firmas exactas de
> `zif_abapgit_repo_online~push`, `zcl_abapgit_stage`, `zcl_abapgit_repo_status`
> contra la versión instalada (vía MCP `get_source`).

## 5bis. Capa de OBJETO genérica (VERIFICADO 2026-06-26 vía MCP get_source)

> Decisión: para **crear/reutilizar cualquier tipo** que abapGit serializa, NO se usa
> la capa de repo (pull/push) sino la **capa de objeto** (`zif_abapgit_object`).
> Esto respeta paquete-por-objeto + OT-por-objeto (no repo=paquete). Transacciones
> (TRAN) y los ~150 tipos salen del mismo motor, sin código por tipo.

**Firmas confirmadas en la developer version instalada (TAILOY):**

- `zif_abapgit_object~deserialize( iv_package TYPE devclass, io_xml TYPE REF TO zif_abapgit_xml_input, iv_step TYPE ty_deserialization_step, ii_log TYPE REF TO zif_abapgit_log, iv_transport TYPE trkorr )` → **paquete Y OT por objeto**.
- `zif_abapgit_object~serialize( io_xml TYPE REF TO zif_abapgit_xml_output )`.
- Steps: `gc_step_id` = EARLY / ABAP / DDIC / LATE / LXE; `get_deserialize_steps( )` indica los del objeto; `get_deserialize_order( )` para dependencias.
- `ZCL_ABAPGIT_OBJECTS_SUPER` (`CREATE PUBLIC`, abstract) constructor: `is_item TYPE ty_item, iv_language TYPE spras, io_files TYPE REF TO zcl_abapgit_objects_files OPTIONAL, io_i18n_params OPTIONAL`. Todos los `ZCL_ABAPGIT_OBJECT_<TIPO>` lo heredan → **instanciación dinámica** por nombre.
- Refuerzo de paquete/OT en el super: `set_default_package` (`EXPORT current_devclass TO MEMORY ID 'EUK'`), `set_default_transport` (`... ID 'EUT'`), `corr_insert`/`tadir_insert` (CTS API). Cubre tipos cuya API no acepta package/trkorr directo.
- `ZCL_ABAPGIT_OBJECTS_FILES`: `new( is_item )`, `set_files( it_files )`, `add_string/add_abap/add_xml`, `read_xml( )`, `get_files( )`.
- `ZCL_ABAPGIT_OBJECTS_FACTORY` solo expone `get_gui_jumper` → la fábrica de objetos (`create_object`) es privada; por eso instanciamos el handler **dinámicamente** (replica de lo que hace abapGit internamente).

**Receta genérica del handler Z (deserialize de 1 objeto):**
```abap
ls_item = VALUE #( obj_type = iv_type  obj_name = iv_name ).
lo_files = zcl_abapgit_objects_files=>new( ls_item ).
lo_files->set_files( lt_files ).                 " .xml (+ fuentes) del payload MCP
CREATE OBJECT li_obj TYPE ('ZCL_ABAPGIT_OBJECT_' && iv_type)
  EXPORTING is_item = ls_item iv_language = sy-langu io_files = lo_files.
DATA(lo_xml) = lo_files->read_xml( ).
LOOP AT li_obj->get_deserialize_steps( ) INTO DATA(lv_step).
  li_obj->deserialize( iv_package = iv_package  io_xml = lo_xml
                       iv_step = lv_step  ii_log = li_log
                       iv_transport = iv_transport ).
ENDLOOP.
" luego activar (zcl_abapgit_objects_activation)
```

**Contrato MCP↔handler (genérico, reemplaza la idea de §4 solo-repo):**
| Verbo/path | Entrada JSON | Qué hace |
|---|---|---|
| `POST /deserialize` | `type, name, package, transport?, files{}` | crea/actualiza CUALQUIER tipo (incl. TRAN) en `package`+`transport` por objeto |
| `GET /serialize` | `type, name` | devuelve los archivos abapGit del objeto |

> Pendiente al codear: mapping de los pocos tipos cuyo nombre de handler no es
> `ZCL_ABAPGIT_OBJECT_<TIPO>` directo; activación tras deserialize; resolución
> headless de decisiones; nodo SICF para exponer el handler por HTTP.
> Objetos de la fábrica se crean en el paquete **local `$7BSADTGIT`**.

## 5ter. Mover paquete / mover OT — ADT NO sirve (verificado en vivo 2026-06-26)

Probado por curl directo contra TAILOY (cliente 100) con un objeto desechable:

- **Asignar/mover objeto a OT:** `POST /sap/bc/adt/cts/transportrequests/{ot}/objects`
  (3 variantes: app/xml name+type, transportorganizer.v1, adtcore:uri) → todas
  `HTTP 400 "user action objects is not supported"` (`SCTS_ADT_MSG 005`).
  **Deshabilitado a nivel sistema.** ADT solo permite LEER la OT.
- **Cambiar de paquete:** `/sap/bc/adt/refactorings/changepackage` y
  `/sap/bc/adt/packageassignments` → `404` (no existen). PUT del metadata del
  programa con `packageRef` cambiado (+`corrNr`) → `200` pero el devclass **no
  cambia** (packageRef no es escribible por esa vía).

➡️ Ambas operaciones se implementan por la **CTS/TADIR API** (lo que usa el super
de abapGit, §5bis): `insert_transport_object` (registrar a OT) y TADIR
`insert_single`/`RS_CORR_INSERT` (recolocar paquete). Se exponen como 2
operaciones más del mismo handler Z (mismo nodo SICF que el puente).

## 6. Credenciales git (Gitea del requerimiento)

Repos privados → abapGit necesita user+token. Opciones:
1. **MCP las pasa en el body** (`git_user/git_token`), leídas de AWS Secrets por
   el MCP, sobre HTTPS. El handler hace
   `zcl_abapgit_login_manager=>set( iv_uri = url  iv_username = u  iv_password = t )`
   antes de `refresh/deserialize/push`. ← recomendado (sin secretos en SAP).
2. Persistir en SAP como background user de abapGit. Más estado en SAP; evitar.

> Confirmar nombre/firma de `zcl_abapgit_login_manager=>set` al implementar.

## 7. Transporte (sistema con cambios transportables)

TAILOY DEV (cliente 100 = Customizing en SCC4, ver memoria SRVB) puede exigir
orden de transporte al deserializar objetos transportables. Decisión a tomar:
- pasar `trkorr` en el body del `/pull` y fijarlo en `ls_checks-transport`, ó
- usar paquete local (`$...`) para lo que no requiera transporte.

## 8. Cambios en el MCP (Python)

`infra/app/abap-mcp/aws-7bs-abap-mcp/src/aws_abap_accelerator/`

1. `sap/sap_client.py` — método nuevo, p.ej.
   `async def call_custom_service(self, path, method, json_body) -> dict`
   que reusa `self.session`, base_url, CSRF y cookies (igual que las llamadas
   ADT) pero apuntando a `/sap/bc/zfabrica/abapgit{path}`.
2. `enterprise_main_tools.py` — registrar tools nuevas (mismo patrón que las
   `aws_abap_cb_*`):
   - `aws_abapgit_link( url, branch, package )`
   - `aws_abapgit_pull( repo_key | url )`
   - `aws_abapgit_status( repo_key | package )`
   - `aws_abapgit_push( repo_key, message )`
   Cada una arma el body, llama `call_custom_service`, devuelve el JSON formateado.
3. Sin cambios de auth: hereda keychain/cert + `x-sap-system-id` actuales.

## 9. Entregables y orden sugerido

1. `ZCL_7BS_ABAPGIT_API` + nodo SICF (empezar por `/link` y `/pull`).
2. Probar pull headless de un repo de prueba a un paquete `$`.
3. `call_custom_service` + tools `link`/`pull` en el MCP.
4. `/status`, luego `/push` (confirmando firmas de repo_online/stage).
5. Resolver credenciales git (§6) y transporte (§7) según el sistema.

## 10. Riesgos / pendientes
- Firmas exactas de push/stage/status/login_manager → verificar por MCP antes de codear.
- Deserialize headless: asegurar que NINGÚN paso abra popup (modo background).
- Transporte en clientes Customizing (§7).
- Despliegue del Z handler **por git** (no copiar a AWS directo) — coherente con
  el flujo de la fábrica; el handler vive en un repo/paquete propio.
- La developer version de abapGit debe permanecer activa (raíz ya arreglada:
  `ZIF_ABAPGIT_AFF_TYPES_V1` quedaba vacía en el pull).
