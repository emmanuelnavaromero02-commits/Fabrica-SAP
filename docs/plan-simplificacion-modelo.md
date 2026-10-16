# Plan de simplificación del modelo de la fábrica

> Decisión 2026-06-14. Objetivo: reducir el modelo de ~15 pasos a ~8, quitando
> duplicación y costuras acumuladas durante el rediseño de diseño/desarrollo.

## Principio rector

**Automatizar la plomería, dejar humano el juicio.** Lo creativo (generar el
paquete de diseño en Claude Code + MCP) y lo externo (VoBos del cliente) se
quedan manuales. Se automatiza o elimina todo lo que es contabilidad de estado,
duplicación de fuentes de verdad y caminos redundantes.

## Modelo actual (~15 pasos)

| # | Paso | Sistema | Quién | Hoy |
|---|------|---------|-------|-----|
| 1 | Recibir requisito (WP + insumos) | OpenProject | SwF Funcional | manual |
| 2 | Cargar documentos al repo | Temporal→Gitea | — | auto |
| 3 | Provisionar repo de diseño (org=cliente) | Temporal→Gitea | — | auto |
| 4 | Generar paquete (spec/prototipo/casos) | Claude Code+MCP | SwF Funcional | manual |
| 5 | (opcional) Borrador con IA | Temporal agente | SwF Funcional | semi |
| 6 | Clarificaciones (loop) | inbox/OP | Funcional | manual |
| 7 | Enviar a Revisión Fábrica | MCP/inbox | SwF Funcional | manual |
| 8 | Estimación + fecha inicial | inbox | SwF Funcional | manual |
| 9 | VoBo Funcional (cliente) | inbox | Funcional cliente | manual |
| 10 | VoBo Usuario-clave (cliente) | inbox | Usuario clave | manual |
| 11 | Fijar fechas | Temporal→OP | — | auto |
| 12 | Provisionar repo de desarrollo | Temporal→Gitea | — | auto |
| 13 | Desarrollo (skill + agente) | Claude Code/agente | SwF Dev | semi |
| 14 | Liberar a pruebas | inbox | SwF | manual |
| 15 | Pruebas → cierre | OpenProject | SwF Tester | manual |

~6 compuertas humanas (4, 6, 8, 9, 10, 14) y 2 provisiones de repo.

## Dónde se ensució (las costuras)

1. **Dos repos / dos fuentes de verdad.** Monorepo legacy `mcp-agent/fabrica`
   (arquitectura, pipeline, paquetes viejos) conviviendo con el repo-por-cliente
   nuevo. El inbox leía de uno y Claude Code escribe en otro.
2. **OpenProject duplica las etapas de Temporal.** Cada gate mueve un status en
   OP y Temporal lleva el stage real → doble contabilidad.
3. **Dos caminos para lo mismo en diseño**: agente automático (`allow_draft`) +
   manual.
4. **Provisión de repo dos veces** (diseño + desarrollo) aunque sea el mismo repo.
5. **Legacy + nuevo pipeline coexisten** (23 workflows viejos, bug "Prototype").
6. **VoBos del cliente partidos en 2 gates secuenciales.**

## Modelo objetivo (~8 pasos)

**Recibir → Provisionar repo → Diseñar en Claude Code (con clarif. embebida) →
Revisión Fábrica → VoBo cliente (1 gate) → Desarrollo → Pruebas → Cierre.**

Con: status de OP derivado de Temporal (1 fuente de verdad), estimación
pre-llenada, un solo repo por requerimiento.

## Qué automatizar vs. mantener manual

**Automatizar (quitar fricción):**
- Sync de status OP ← Temporal (una sola fuente de verdad).
- Estimación pre-llenada por agente; el Funcional solo aprueba.
- Una sola provisión de repo (reusar diseño→desarrollo).

**Mantener manual (criterio/cliente):**
- Generar el paquete en Claude Code (corazón del modelo).
- VoBos del cliente (externos por naturaleza).
- Liberar a pruebas (decisión de calidad).

**Simplificar/colapsar:**
- Un solo repo como fuente de verdad del paquete (el de org=cliente).
- Fusionar los 2 VoBos en 1 gate con checklist.
- Quitar el camino auto-agente como gate (dejar solo "borrador inicial").
- Clarificaciones dentro del gate de diseño, no como estado aparte.

## Plan por fases (1 deploy c/u, ordenado por riesgo creciente)

### Fase 0 — Línea base limpia · riesgo bajo
- Terminar workflows legacy (mínimo `requisito-97`, crash-loop de 2663 intentos
  por el WP hijo tipo "Prototype" inexistente; decidir el resto de los 23).
- Confirmar que el pipeline nuevo NO crea el WP hijo "Prototype".
- Documentar el modelo objetivo (este archivo).
- **Resultado:** solo el pipeline nuevo vivo, sin bucles de error.

### Fase 1 — Un solo repo · riesgo medio
- `provisionar_repo` corre una vez (phase diseño) y se reusa en desarrollo →
  quitar el segundo bloque `provisionar_repo`.
- Auditar y reapuntar cualquier lectura/escritura del paquete que aún use
  `mcp-agent/fabrica` → todo al repo-por-cliente. (Verificar inbox `renderDiseno`,
  `_read_block_state`, y `publicar_diseno` del MCP.)
- `mcp-agent/fabrica-arquitectura` queda solo para config global (arquitectura,
  pipeline, skills).
- **Resultado:** 1 repo de trabajo por requerimiento; el monorepo deja de
  acumular ramas `req-N`.

### Fase 2 — Quitar OpenProject (la vista de proyecto vive en el Panel) · riesgo alto
Decisión 2026-06-14: **eliminar OpenProject**. Temporal = verdad del lifecycle;
el Panel = vista de proyecto. Una sola fuente de verdad (Temporal + Gitea).

| Función de OP | Reemplazo |
|---|---|
| Crear requisito | Formulario en el Panel → `POST /factory/start` (ya existe en webhook.py) |
| Disparador por cambio de status | Desaparece (lo arranca el Panel) |
| Estado / fechas / comentarios | JSON en el repo (timeline.json, plan.json) — Temporal escribe ahí |
| Segmentación por "proyecto" | "cliente + grupo/etiqueta"; atributos Keycloak `proyectos`/`capacidades` dejan de usar ids de OP |

Tareas: quitar/redirigir las activities `set_status`, `set_dates`, `op_comment`,
`op_create_child`, y las lecturas `api/wp/{id}` del inbox/Panel (de OP → repo).

**Migración sin riesgo:** construir primero la vista de proyecto en el Panel
leyendo de Temporal+Gitea **con OP todavía vivo**; verificar que cuadra; luego
cortar OP. (Sub-fase 2a vista en paralelo, 2b corte de OP.)

**Resultado:** sin OpenProject; estado y proyecto solo en Temporal + Gitea + Panel.

## Capacidad — vista de proyecto en el Panel (reemplazo de OP)

Necesidad: llevar fechas inicio/esfuerzo/fin/tiempo invertido y avance, ágil,
para responder estimados y avance. Reqs que separan **front/back** se reportan
por separado.

Decisiones (2026-06-14): vive en el **Panel** (extender, no app nueva); tiempo
**derivado automático**; front/back = **subtareas dentro del requisito**; avance
**por etapas del pipeline**.

**Arquitectura de datos (CQRS-lite) — frontera clara de qué vive dónde:**

| Capa | Fuente de verdad | Dónde |
|---|---|---|
| Lifecycle / etapa | **Temporal** (transaccional, durable) | Temporal DB |
| Artefactos (spec, assertions, prototipo, código, entregables, historial) | **Git** (versionado, diff) | Gitea |
| Datos de proyecto (estimado, fechas, tiempo, avance, front/back, registro, segmentación) | **Postgres** (read-model + datos PM) | DB |

- Se escribe en Temporal/Git (la verdad); se lee de la DB (ágil, indexado). La DB
  es una **proyección**: si se desincroniza, se **reconstruye** desde Temporal+Git
  (no es 2da fuente de verdad del lifecycle).
- Resuelve los 2 males del "repo como DB": (a) la vista de proyecto NO recorre
  repos → consulta instantánea; (b) los datos PM mutables se escriben
  **transaccionalmente** → adiós a las carreras de read-modify-write sobre JSON.
- **Reutilizar el Postgres que libera OpenProject** (`fabrica-aws-db-1`) → cero
  infra nueva; absorbe también lo relacional que hacía OP.
- **NO** meter artefactos (spec/assertions/código/entregables) en la DB → quedan
  en Git, versionados (base del "documento vivo").

**Jerarquía de dominio (SIN portafolio/programa, decisión 2026-06-14):**
`Cliente → Proyecto → Capacidad → Requerimiento`, más `Requerimiento ↔ Requerimiento`
(links, típicamente CR). Esto absorbe lo relacional que hacía OP y resuelve la
segmentación: los atributos Keycloak `proyectos`/`capacidades` referencian estas
tablas, no ids de OP.

**Modelo de datos PM (en Postgres, alimentado como proyección):**
- `cliente` — id, nombre, slug (= org Gitea).
- `proyecto` — id, cliente_id, nombre.
- `capacidad` — id, proyecto_id, nombre (segmentación fina de usuarios).
- `requisito` — id, proyecto_id, capacidad_id?, título, stack, talla, inicio/fin plan,
  etapa actual, avance, repo (org/name/branch).
- `subtarea` — front/back: requisito_id, tipo, stack, talla, fechas, responsable, estado.
- `requisito_relacion` — origen_id, destino_id, tipo (`cr` | `relacionado`),
  `hereda_repo` (bool).
- `tiempo_etapa` — requisito_id/subtarea_id, etapa, entrada, salida, horas_laborales.
- `calendario` (config) — `tz`, días laborales, horario (p.ej. 09:00–18:00), feriados.
- `talla_horas` (config) — `stack`, `talla` (tiny|small|medium|large|verylarge) → `horas`.
  PK (stack, talla). Editable por admin en el Panel.

**Estimación por camisas (t-shirt sizing):** el estimado es una **talla**, no un
número quemado. El estimado en horas = `lookup(stack, talla)` en `talla_horas`;
el total del requisito = suma de sus subtareas. Front (p.ej. React) y back (p.ej.
ABAP) pueden tener la misma talla y distintas horas según su stack.

**Seguridad = Roles × Proyectos × Capacidades:**
- Roles → Keycloak (SSO): qué tipo de usuario.
- Asignaciones → DB: `usuario_proyecto`, `usuario_capacidad` (reemplazan los
  atributos Keycloak `proyectos`/`capacidades`; una sola fuente consultable).
- Acceso a un requisito = rol permitido AND usuario asignado a su proyecto AND
  (si tiene capacidad) asignado a esa capacidad. El Panel/inbox filtran con esa regla.

**CR / herencia de repo:** al crear un requisito y ligarlo a otro (relación/CR),
el Panel pregunta **"¿heredar todo el repo de \<req origen\>?"**:
- Sí → `provisionar_repo` clona el repo del origen (spec/assertions/prototipo/código)
  como baseline; el CR construye encima (encaja con "CR dispara v{n+1}"). Se añade
  un parámetro `heredar_de` al provisioning.
- No → repo nuevo desde template; CR independiente.
Se guarda `hereda_repo` en `requisito_relacion` para trazabilidad.

Los artefactos y el `timeline.json` (proyección que escribe Temporal) siguen en
el repo; la DB se alimenta de esos eventos + del plan.

**Tiempo invertido = HORAS LABORALES.** Entre entrada y salida de cada etapa se
cuentan solo horas laborales según `calendario.json` (descuenta noches, fines de
semana y feriados). La etapa en curso cuenta hasta *ahora*. Toggle para separar
**tiempo de trabajo** (diseño/desarrollo/pruebas) de **tiempo de espera**
(esperando VoBo del cliente), para no inflar el esfuerzo del equipo con esperas.

**Avance = etapa actual / total de etapas** (orden de las ~8 etapas, desde
Temporal). En desarrollo, ponderado por completitud de las subtareas front/back.

**Vista:** tabla/board ágil, una fila por requisito + subfilas front/back;
columnas estimado · inicio · fin · tiempo invertido (laboral) · % avance. Se
arma sola desde Temporal + los JSON.

### Fase 3 — Menos compuertas · riesgo medio
- Fusionar VoBo funcional + usuario-clave en 1 gate con checklist.
- Clarificaciones dentro del gate de diseño (no estado aparte).
- Estimación pre-llenada por agente → el Funcional solo aprueba.
- **Resultado:** de ~6 gates a ~4.

### Fase 4 — Limpieza final · riesgo bajo
- Quitar el camino auto-agente como gate (dejar solo borrador opcional).
- Retirar código muerto del modelo viejo.

## Capacidad transversal — "documento vivo" (sync diseño ↔ código)

**Problema:** a lo largo del camino hay cambios (en diseño, y en código que
redefine el diseño). Hoy no hay vía limpia para que, en desarrollo, Claude Code
actualice spec/assertions sin que se desincronicen (drift), y `publicar_diseno`
escribe al monorepo (línea 475: `o, r = FABRICA_OWNER, FABRICA_REPO_NAME`).

**Regla de gobernanza (la señal es `assertions.json`):**

| Clase | Cuándo | Escritura | Gobernanza |
|---|---|---|---|
| Refinamiento | `motivo=refinamiento` y NO cambian las AS | sobreescribe vigente + `historial-aprendizaje.md` | ninguna, sigue |
| CR/Defecto | `motivo∈{cr,defecto}` sin cambio de AS | `v{n+1}` snapshot + historial | comentario al funcional |
| Cambio de contrato | cambian las AS (agregar/quitar/modificar) | `v{n+1}` + marca "contrato cambiado" | re-VoBo del cliente |

**Tool MCP `sincronizar_diseno`** (usable en desarrollo, repo del cliente):

```python
sincronizar_diseno(
    motivo: str,               # "refinamiento" | "cr" | "defecto"
    resumen: str,              # qué cambió y por qué -> historial
    spec_md: str = "", assertions: Any = None,
    test_plan_md: str = "", prototipo_html: str = "",
    docs_soporte: list[dict] | None = None,
    commit: str = "",          # SHA del cambio de código (trazabilidad)
    actor: str = "", org="", repo="", wp="", branch=""
) -> dict   # {clase, version, escrito[], as_diff, gobernanza, url}
```

Comportamiento: (1) resuelve repo del cliente; (2) lee `assertions.json` vigente
y calcula diff de AS por id; (3) clasifica según la tabla; (4) escribe vigente y,
si hay bump, snapshot en `especificacion/versiones/v{n+1}/`; (5) append a
`historial-aprendizaje.md` (ts, motivo, resumen, commit, diff AS, versión);
(6) actualiza `meta.json`; (7) dispara la gobernanza correspondiente.

Como todo vive en UN repo (Fase 1), código + spec + assertions se versionan
juntos en el mismo commit/PR; git da el diff y la trazabilidad. El MCP solo
impone estructura/versión y emite la señal de contrato.

**Dependencias:** Fase 1 (un repo) + arreglar `publicar_diseno` (quitar hardcode
al monorepo) + señal "contrato cambiado" en el workflow que re-abra el VoBo
(sub-paso de Fase 3).

## Decisión sobre los workflows legacy

23 workflows `requisito-N` (modelo pre-rediseño) siguen Running. Ninguno está en
el flujo nuevo. `requisito-97` está en crash-loop real. El resto está parado en
compuertas (clarificación, revisión de diseño, estimación) — inofensivos pero no
migrables sin re-hacer el diseño a mano. Pendiente decidir: terminar solo #97,
o reset total para arrancar limpio con el modelo nuevo.
