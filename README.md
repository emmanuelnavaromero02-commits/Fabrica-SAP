# 🏭 Fábrica SAP — beta mínima

Fábrica de software SAP con agentes de IA. Cada requisito pasa por etapas fijas; los
agentes hacen el trabajo y las personas solo deciden en las compuertas. Todo lo que hace
un agente se **verifica automáticamente**. Si falla, la tarea **escala a un modelo más
capaz**, que recibe el intento anterior con sus errores.

```
Recepción 🤖 → Diseño 🤖 → Aprobación cliente 👤 → Construcción 🤖 → Revisión ABAP 👤 → UAT 👤 → Cerrado
```

## Probarlo en 2 minutos (sin llaves de API)

Requisitos: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 22+ y git.

```bash
make install        # dependencias del backend (uv) y del frontend (npm)
make api            # terminal 1 → http://localhost:8000/docs
make web            # terminal 2 → http://localhost:5173
```

Por defecto corre en modo **mock**: los agentes y SAP están simulados, así que no hace
falta ninguna llave. En la web:

1. Crea un requisito **sin** especificación. El Analista le hace una pregunta al cliente
   y el requisito queda bloqueado.
2. Responde la pregunta en la pestaña **Tablero**. Los agentes siguen solos hasta la
   aprobación del cliente.
3. Aprueba como `Funcional`. En **Construcción** vas a ver el escalamiento:
   - N2 (Codex y Sonnet) falla por `SELECT *` y por una aseveración sin cumplir.
   - N3 (Opus) lo corrige.
   - El revisor es de otro proveedor.
4. Cambia el rol arriba a la derecha: aprueba como `Consultor ABAP` y luego como
   `Usuario clave`. El requisito queda cerrado.

Para el entorno completo (Postgres, Temporal, API, worker y web), usa `make up`, que
ejecuta `docker compose up --build`.

## Arquitectura

```
 web/ (React + TypeScript)  ──HTTP──►  API FastAPI ──► Motor de etapas ──► Steps (agentes)
                                         │                 ▲                   │
                                         │  inline / Temporal (kick)           ▼
                                         ▼                               Router de escalamiento
                               Postgres = tablero                         N1 → N2 → N3 → N4 → persona
                    (mensajes tipados, decisiones, intentos,                  │
                       costos, auditoría SAP)                                 ▼
                                                          Gateway de modelos (Claude · OpenAI · Codex · mock)
                                                                              │
          Claude Code / Codex  ──MCP──►  fabrica (tablero)          Verificadores ──► Puente SAP (reglas)
                                         sap (Puente SAP)                               └ SAP DEV simulado
                                                                  Git por requisito (local o Gitea)
```

| Módulo (`backend/src/fabrica/`) | Qué hace |
|---|---|
| `catalog.py` + `config/*.yaml` | Niveles de modelos, políticas por actividad y máquina de etapas. Se cambian sin tocar código. |
| `domain/` | Reglas de transición y permisos de las compuertas. Contratos del API. |
| `db/` | Modelo de datos del tablero (SQLAlchemy async; Postgres o SQLite). |
| `blackboard/` | Operaciones del tablero: mensajes tipados, respuestas, intentos, auditoría. |
| `llm/` | Gateway y proveedores: Anthropic (pensamiento adaptativo, `effort`, JSON estricto, respaldo ante rechazos), OpenAI Responses, Codex (`codex exec`) y mock. |
| `escalation/` | Router N1→N4 con paquete de relevo, revisión cruzada entre proveedores y tope de gasto. |
| `verifiers/` | Verificación de la spec y del ABAP en DEV: sintaxis → activación → ATC → pruebas. |
| `sap/` | Puente SAP: escribe solo en DEV y en paquetes Z/Y, nunca libera transportes, audita todo. Por ahora simulado. |
| `git/` | Un repositorio por requisito: git local o API de Gitea. |
| `agents/` | Roles: clasificador, analista, arquitecto, desarrollador, revisor y documentador. |
| `pipeline/` | Motor de etapas, comandos de personas y runners (inline o Temporal). |
| `workflow/` | Flujo y worker de Temporal (`requisito-{id}`). |
| `api/` | Rutas REST. La identidad llega por cabeceras en la beta; en producción, OIDC. |
| `mcp_servers/` | MCP `fabrica` y `sap` para Claude Code, Codex y VS Code. |

**Reglas de diseño**
- Cada archivo tiene como máximo 300 líneas.
- La base de datos es la fuente de verdad. Temporal solo ejecuta.
- Donde no hay verificador automático (el diseño), el trabajo empieza en N3.
- Quien revisa nunca es del mismo proveedor que quien escribió.
- La identidad de quien decide sale de la sesión, nunca de un campo de texto libre.

## Modo real (`FABRICA_LLM_MODE=live`)

```bash
cp .env.example .env    # pon ANTHROPIC_API_KEY y OPENAI_API_KEY
codex login             # para el proveedor Codex
```

Los modelos por nivel están en `config/models.yaml`. Los IDs de OpenAI (`gpt-6-sol`,
`gpt-6-luna`) y **sus precios son provisionales**: confírmalos en la documentación
oficial antes de usarlos. Revisa también los flags de `codex exec --help` y ajústalos en
`llm/codex_provider.py` si tu versión de la CLI cambió.

## MCP en Claude Code y Codex

- **Claude Code:** `.mcp.json` en la raíz ya registra `fabrica` y `sap`. Abre Claude
  Code en el repo y aprueba los servidores.
- **Codex:** copia `config/codex.example.toml` a `~/.codex/config.toml` y ajusta la ruta.

## Calidad

```bash
make check          # ruff + mypy estricto + tsc + pytest
```

La prueba `test_flow.py` recorre el flujo completo por el API. `test_temporal.py` corre
el flujo real en Temporal; si no puede descargar el servidor de desarrollo, se omite.
Para probar contra Postgres:
`FABRICA_TEST_DATABASE_URL=postgresql+asyncpg://… make test`.

## Qué falta para producción

- **Puente SAP real** (ADT / abap-adt-mcp) por cliente y sistema, con credenciales en
  un gestor de secretos.
- **Identidad:** OIDC con Keycloak en el API y en los MCP, con token por usuario, en
  lugar de las cabeceras de la beta.
- **Migraciones con Alembic.** Hoy las tablas se crean al arrancar.
- **Router que aprenda:** elegir el nivel inicial según la tasa de éxito histórica
  (los datos ya están en `/api/usage`).
- **Contenedor aislado por requisito** para los agentes que programan (Codex o Claude
  Agent SDK).
- **Temporal de producción** con permisos por rol y retención larga, en lugar del
  servidor de desarrollo.
