# Arquitectura de la fábrica (infraestructura del orquestador)

> **Nota:** este documento es para quien **desarrolla la fábrica** (el
> orquestador: Temporal, agentes, OpenProject, Gitea). **NO** se inyecta a los
> agentes de la fábrica — su `arquitectura.md` (la premisa cacheada) describe
> solo la **plataforma objetivo SAP**. Esta parte se separó de ahí porque no
> aporta valor al especificar capacidades SAP y solo diluía el contexto.

Describe el sistema que **corre la fabrica** — Temporal, los agentes,
OpenProject, Gitea. Relevante cuando se proponen cambios a las propias
activities o al pipeline.

## Stack tecnologico (fabrica)

- **Plano de control humano**: OpenProject 17 (Ruby on Rails 8, PostgreSQL
  17, Memcached). Auth Basic con tokens API por usuario de servicio.
- **Control de versiones de artefactos**: Gitea 1.26 (Go, PostgreSQL 17,
  SSH puerto 2222).
- **Orquestacion**: Temporal 1.29 con PostgreSQL 16 dedicado. Workers en
  Python 3.13 con `temporalio` SDK; webhook receiver en FastAPI/Uvicorn.
- **Agentes de IA**: Anthropic API (Claude Sonnet/Opus 4) con prompt
  caching ephemeral (TTL 5 min). Tool-use para salida estructurada.
- **Reverse proxy / TLS**: Caddy con Let's Encrypt automatico (despliegue
  AWS).
- **Despliegue**: Docker Compose sobre EC2 (v1). Evolucion futura a ECS
  Fargate con ALB.

## Patrones arquitectonicos vinculantes (fabrica)

### Workflows durables
Cada Requisito que entra a la fabrica corre como un workflow de Temporal con
event history completo (fuente canonica de auditabilidad). Toda capacidad
multi-paso o de larga duracion DEBE modelarse como workflow Temporal — no
endpoint con estado en memoria, no cron.

### Compuertas humanas como signals
Las pausas para aprobacion humana son `signal` de Temporal — workflow
dormido durable, no consume slot, sobrevive reinicios, se reanuda cuando un
humano dispara el cambio (webhook OpenProject o API del orquestador).

### Frontera estructural por permisos
El usuario `fabro-agent` en OpenProject tiene rol restringido cuyo workflow
del tipo `Requisito` NO contiene ninguna transicion hacia estados de
aprobacion (`Aprobacion funcional`, `VoBo del cliente`, `Aprobacion del
desarrollo`, `UAT Approved/Rejected`, `Functional Testing Approved`). La
frontera la hace cumplir la API de OpenProject, no convencion.

### Idempotencia de activities
Cada activity de Temporal es idempotente. Convenciones:
- Escritura de archivos en Gitea: `sha` para PUT (update-if-exists).
- Transiciones de estado en OpenProject: `lockVersion` como
  compare-and-swap.
- Creacion de WPs hijos: comprobacion previa por convencion de naming.

### Pipeline declarativo
El workflow es un **interprete de `pipeline.yaml`** (en
`mcp-agent/fabrica-arquitectura`). Agregar/reordenar bloques = editar YAML +
PR, sin tocar codigo. Tipos: `load_inputs`, `agent`, `set_status`,
`human_gate`. Anadir un tipo nuevo = extender el match en `workflow.py`.

### Aislamiento por contenedor
Cada componente corre en su propio contenedor con su propio usuario, su
propio token, sus propios secretos. Sin docker socket compartido, sin
secretos cruzados.

## Integracion con OpenProject (fabrica)

- API v3 REST. Auth Basic con usuario literal `apikey` y token como
  contrasena.
- Comentarios: `POST /api/v3/work_packages/{id}/activities` con
  `{comment:{raw:"..."}}`. Requiere `add_work_package_comments` (distinto
  de `add_work_package_notes`).
- Transicion: `PATCH /api/v3/work_packages/{id}` con `lockVersion` +
  `_links.status.href`. Si no esta en el workflow del rol -> 422.
- Webhooks salientes: configurados en Administracion. Firma HMAC-SHA1; el
  receptor del orquestador valida o devuelve 401.
- Adjuntos: `GET /api/v3/work_packages/{id}/attachments` lista; cada uno
  expone `_links.downloadLocation.href`. Filtrar por `contentType` antes
  de pasar a un agente.

## Integracion con Gitea (fabrica)

- API v1, base `/api/v1`. Auth `Authorization: token <pat>`.
- Token de `fabro-agent`: scopes `read:user`, `write:repository`,
  `write:issue`. Sin admin ni organizacion.
- Convenciones:
  - Una rama por requisito: `req-<id>` (off `main`).
  - Cada commit cita `refs #<id>`.
  - Estructura por requisito: `inputs/`, `especificacion/`, `prototipo/`,
    `desarrollo/`, `pruebas/`.
  - Fabro **no** hace merge a `main` — la integracion queda sujeta a
    revision humana.

## NFRs de la fabrica (baselines)

- Latencia sincrona del orquestador: P95 < 2s en `/factory/start`,
  `/factory/{id}/decision`, `/factory/{id}/clarified`,
  `/webhooks/openproject`, `/health`. Activities asincronas: timeout por
  tipo (agente 10 min, APIs externas 60s).
- Disponibilidad: 99% mensual (v1 single-EC2).
- Auditabilidad: todo cambio trazable a actor (humano o `fabro-agent`) con
  timestamp UTC. Event history de Temporal es la fuente canonica.
- Seguridad: TLS extremo-a-extremo via Caddy. Secretos en SSM (AWS) o
  `.env` (local). Webhooks firmados (HMAC). Adjuntos filtrados por
  `contentType`.
- Sandbox: los agentes corren como activities en el worker; no comparten
  docker socket ni secretos con OpenProject/Gitea. Cuando se agregue
  ejecucion de codigo, ira en un sandbox dedicado.

## Restricciones para nuevas features de la fabrica

1. Endpoint nuevo del orquestador que reciba data externa DEBE validar
   firma HMAC; sin firma = 401.
2. Nueva activity de Temporal DEBE ser idempotente — explicar por que en
   el docstring.
3. Interaccion con OpenProject que cambie estado DEBE usar el usuario de
   servicio adecuado (no admin) y respetar su workflow.
4. Nuevo tipo de adjunto procesado DEBE filtrarse por `contentType`.
5. Nuevo agente que use la API de Anthropic DEBE usar prompt caching para
   los prefijos estables (instrucciones + arquitectura).
6. Nuevo bloque del pipeline va al `pipeline.yaml` antes que al codigo
   Python, salvo que requiera un tipo de bloque nuevo.
