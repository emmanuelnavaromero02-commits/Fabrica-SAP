# Orquestador de la fábrica de software

Motor de la **dark factory**: orquesta el pipeline automatizado que conecta
los requisitos de OpenProject con agentes de IA y versiona los artefactos en
Gitea. Es desarrollo **propio** sobre **Temporal** — no es Fabro (ver la nota
de arquitectura más abajo).

## Por qué Temporal

El pipeline tiene que **pausarse en compuertas humanas** (a veces días),
**sobrevivir reinicios**, **reintentar** etapas que fallan y dejar **traza de
auditoría**. Eso es exactamente *durable execution*, y Temporal lo da probado.
El principio del brief —"ejecución a oscuras, verificación encendida"— se
vuelve ejecutable: las etapas automáticas son *activities*; las compuertas
humanas son *signals* que el workflow espera de forma durable.

## Arquitectura

```
OpenProject  --webhook-->  webhook (FastAPI)  --start/signal-->  Temporal
                                                                   │
                                              FactoryWorkflow  <----┘
                                                   │ activities
                                   ┌───────────────┼───────────────┐
                              agente de IA     OpenProject        Gitea
                            (seam: agent.py)    (API v3)         (API v1)
```

| Servicio | Imagen | Función |
|----------|--------|---------|
| `temporal` | `temporalio/auto-setup:1.29.6.1` | Motor de orquestación durable |
| `temporal-ui` | `temporalio/ui:2.50.0` | Consola web (`127.0.0.1:8233`) |
| `temporal-db` | `postgres:16` | Estado de Temporal |
| `worker` | build local | Ejecuta `FactoryWorkflow` y sus activities |
| `webhook` | build local | Webhook de OpenProject + API de control (`127.0.0.1:8000`) |

## El pipeline (v1)

Modela las primeras filas de la sección 9 del brief:

```
Requisito "Asignado a la fábrica"
  → agente de especificación (asistido)  → spec.md a Gitea + comentario en OP
  → agente de prototipo (asistido)       → WP hijo "Prototipo" + Gitea
  → estado OP: "En revisión"
  → ⏸ COMPUERTA HUMANA  (aprobación funcional / VoBo del cliente)
  → agente de scripts de prueba (autónomo) → scripts.md a Gitea
  → estado OP: "En desarrollo"
```

Cada commit cita el work package (`refs #<id>`) y va a una rama por requisito
(`req-<id>`), según las convenciones del brief (8.3).

## Requisitos

- El stack de [`../local`](../local) **arriba** (crea la red que el orquestador
  comparte para alcanzar OpenProject y Gitea).
- Docker Desktop.
- Un repositorio en Gitea para los artefactos de la fábrica.

## Puesta en marcha

```powershell
cd orquestador
Copy-Item .env.example .env     # rellenar TEMPORAL_DB_PASSWORD, tokens de
                                 # Gitea, y FACTORY_GITEA_OWNER / _REPO
docker compose up -d --build
```

Y luego provisiona el usuario "Fabro" en OpenProject con la frontera
estructural del brief 8.4:

```powershell
orquestador\scripts\setup-fabro.ps1
```

Este script (vía `rails runner` dentro del contenedor `web`) crea los
estados del brief, el usuario `fabro-agent`, el rol "Agente Fabro" con
**workflow restringido que NUNCA admite transiciones a estados de
aprobación**, da de alta al usuario en los proyectos, genera su token de
API y lo escribe en `orquestador/.env`. Es idempotente.

Comprueba: la consola de Temporal en **http://localhost:8233**, y
`docker compose logs worker` debe decir *"Worker iniciado"*.

## Operación

La API de control está en `http://localhost:8000`:

```powershell
# Arrancar un workflow a mano (sin depender del webhook)
curl -X POST http://localhost:8000/factory/start -H "Content-Type: application/json" `
  -d '{"work_package_id":42,"project_id":1,"subject":"Login con SSO","description":"..."}'

# Ver la etapa actual
curl http://localhost:8000/factory/42

# Soltar la compuerta humana (aprobar / rechazar)
curl -X POST http://localhost:8000/factory/42/decision -H "Content-Type: application/json" `
  -d '{"decision":"approved"}'
```

Cada run es visible y auditable en la consola de Temporal: etapas, tiempos,
reintentos, el historial completo.

### Webhook de OpenProject

En OpenProject → *Administración → Webhooks*, apunta un webhook a
`http://webhook:8000/webhooks/openproject` (activado en creación/actualización
de work packages). Cuando un Requisito pasa a *Asignado a la fábrica* el
workflow arranca; cuando alcanza el estado de VoBo, la compuerta se suelta.
Configura `OPENPROJECT_WEBHOOK_SECRET` para validar la firma (el brief lo
exige en producción).

## Lo que es seam / v1

- **El agente** ([`agent.py`](app/agent.py)) es el punto de integración: v1
  genera el entregable con la API de Anthropic si hay `ANTHROPIC_API_KEY`, o
  un marcador determinista si no. Aquí EPI-USE enchufa la lógica real de sus
  agentes (o el Claude Agent SDK para etapas que editen código).
- El pipeline implementa 3 etapas + 1 compuerta. Las etapas restantes de la
  sección 9 (andamiaje, lógica de negocio, ejecución de pruebas, evidencia)
  son **el mismo patrón**: una activity `agent_stage` más escritura a
  Gitea/OpenProject.

## Seguridad

- **Frontera estructural verificada.** El rol "Agente Fabro" que crea
  `setup-fabro.ps1` no tiene en su workflow ninguna transición hacia
  *Aprobación funcional*, *VoBo del cliente* ni *Aprobación del desarrollo*
  (brief 8.4). Si el orquestador intenta esas transiciones, OpenProject las
  rechaza con HTTP 422 — comprobado en vivo. No es una promesa por
  convención: la API lo hace cumplir.
- El webhook **valida la firma** de cada payload si `OPENPROJECT_WEBHOOK_SECRET`
  está definido.
- Secretos solo en `.env` (gitignored).

## Relación con AWS y con Fabro

- **AWS:** se cambian las URLs de `.env` por las públicas y Temporal pasa a un
  despliegue multi-servicio sobre RDS. El código del orquestador no cambia.
- **No es Fabro.** Se evaluó `fabro-sh/fabro`: integra solo con GitHub (no con
  OpenProject/Gitea) y forkearlo era inviable. Este orquestador toma de Fabro
  el *diseño* (pipeline como grafo de etapas tipadas, compuertas humanas de
  primera clase) y lo implementa sobre Temporal, sin la deuda de un fork.

## Solución de problemas

- **`worker` reinicia / "no se pudo conectar a Temporal"** — Temporal tarda en
  el primer arranque (crea el esquema). El worker reintenta ~2 min; espera.
- **Activity de OpenProject falla con HTTP 400 "Invalid host_name"** —
  `OPENPROJECT_HOST_HEADER` debe valer `localhost:8080` en local.
- **`op_set_status` falla con HTTP 422 "no valid transition"** — Esa
  transición no está en el workflow del rol "Agente Fabro". Si es operativa,
  añádela editando `scripts/create-fabro-agent.rb` (lista `fabro_transitions`)
  y re-ejecuta `setup-fabro.ps1`. Si es a un estado de aprobación, **está
  bien** que falle (brief 8.4) — el orquestador no debería intentarlo.
- **`op_comment` falla con HTTP 404** — el rol del usuario carece de
  `add_work_package_comments`. `setup-fabro.ps1` lo añade tanto a "Agente
  Fabro" como a "Agente MCP"; re-ejecútalo si vienes de una versión previa.
- **`up` falla por la red `reqplatform-local_default`** — el stack de
  `../local` no está arriba; levántalo primero.
