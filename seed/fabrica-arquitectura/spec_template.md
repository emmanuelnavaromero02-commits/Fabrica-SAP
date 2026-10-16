# {Título del requisito}

> **Resumen ejecutivo (3 frases máx):** qué entrega, para quién, qué valor de negocio.

> **Convenciones del stack:** las convenciones específicas de la tecnología
> objetivo (nombres de objetos, jerga, controles de UI válidos, NFRs típicos)
> las dicta el **skill de diseño del perfil** (`<skill_de_diseno>`). Esta
> plantilla es **neutral**: define la estructura común y el contrato SDD (§7).

## 1. Contexto

| Campo | Valor |
|---|---|
| Actor primario | (rol que ejecuta) |
| Proceso de negocio | (cadena de valor donde encaja) |
| Frecuencia de uso | (diaria / por evento / etc.) |
| Ventana operativa | (horario crítico si aplica) |
| Sistema(s) afectados | (el/los sistema(s) o servicios impactados) |

## 2. Actores y permisos

| Actor | Rol | Permiso requerido | Acciones |
|---|---|---|---|
| {ej. Operador} | {rol/grupo del sistema objetivo} | Crear/modificar/eliminar registros; leer maestros | {captura diaria} |
| {ej. Administrador} | {rol admin} | Mantenimiento de catálogos/maestros | {altas/bajas, configuración} |
| {ej. Usuario de lectura} | {rol lectura} | Solo lectura + reportes | {consulta y exportación} |

**Nota:** la autorización es parte del contrato — declara qué permiso/rol se
valida antes de cada operación (la forma concreta la define el skill del stack).

## 3. Alcance

**Incluye:** _(bullets cortos, máx 7)_

- ...

**Excluye:** _(qué deliberadamente queda fuera — referencia otro REQ si corresponde)_

- ...

## 4. Vistas y comportamiento (casos de uso compactos)

_Esta tabla se alinea 1:1 con las vistas del prototipo. Cada fila = un caso de uso completo en una sola línea visual._

| CU | Vista | Actor | Trigger | Flujo principal (pasos cortos) | Postcondición | Componentes UI |
|---|---|---|---|---|---|---|
| CU1 | {Registro Diario} | {Operador} | {Inicio de turno} | 1·Abre la vista → 2·Carga datos previos → 3·Edita campos → 4·Guarda | {Registros del día persistidos} | {tabla editable, búsqueda, toolbar "Guardar"} |
| CU2 | {Registro Diario} | {Operador} | {Agregar entrada extra} | 1·"+ Agregar" → 2·Completa campos → 3·Guarda | {Fila nueva persistida} | {botón, fila editable} |
| CU3 | {Histórico} | {Operador / Lectura} | {Consulta días pasados} | 1·Filtros → 2·Tabla read-only → 3·Click fila → Detalle | {Sin cambios (read-only)} | {tabla ro, filtros} |
| CUn | ... | ... | ... | ... | ... | ... |

## 5. Datos y campos (todas las entidades del requisito)

_Documenta **TODAS** las entidades que el requisito necesita (transaccionales,
maestros/catálogos, relaciones), no solo la principal. Una sub-tabla por entidad.
Marca el rol de cada una y, si hay relaciones, descríbelas al final._

### 5.1 {Entidad principal} _(transaccional)_

| Campo | Tipo | Origen | Editable | Reglas / validaciones |
|---|---|---|---|---|
| ID | {id} | secuencia | no | autogenerado |
| {Campo A} | {tipo} | {catálogo/maestro} | sí | debe existir |
| ... | ... | ... | ... | ... |

### 5.2 {Entidad secundaria / maestro / catálogo} _(maestro)_

| Campo | Tipo | Origen | Editable | Reglas / validaciones |
|---|---|---|---|---|
| {Clave} | {tipo} | {origen} | {sí/no} | {regla} |
| ... | ... | ... | ... | ... |

### 5.n {Otra entidad requerida}

| Campo | Tipo | Origen | Editable | Reglas / validaciones |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

**Relaciones entre entidades:** _(cardinalidad y llaves; ej. "5.1 N:1 5.2 por
campo X", "5.1 1:N 5.3 por campo Y"). Si no hay, escribe "No aplica"._

## 6. Reglas de negocio

_Una frase por regla. Verbo en presente._

- **R1.** {Una entidad solo puede estar en un estado por periodo.}
- **R2.** {Un campo se autocompleta al elegir otro.}
- **R3.** ...

## 7. Criterios de aceptación (aseveraciones verificables · contrato SDD)

> **Contrato máquina-ejecutable.** Cada fila es una aseveración ATÓMICA con ID
> estable `AS-xx`. El "Entonces" debe ser comprobable por máquina (valor exacto,
> conteo, estado final, mensaje literal, código HTTP) — nada de "funciona bien"
> ni "responde rápido". Cada regla de negocio (§6) origina ≥1 aseveración; el
> `test-plan.md` referencia cada `AS`. El agente emite además estas mismas filas
> en el campo estructurado `assertions` (sidecar `especificacion/assertions.json`)
> para que el robot de desarrollo (SDD) las ejecute sin parsear Markdown. La
> tabla de abajo y el JSON deben coincidir 1:1.

| ID | Dado (estado/contexto) | Cuando (acción/evento) | Entonces (resultado verificable) | Regla | Datos |
|----|------------------------|------------------------|----------------------------------|-------|-------|
| AS-01 | {entidad E001 con relación ya asignada} | {abre el selector de relaciones de E001} | {la relación ya asignada NO aparece en la lista} | R1 | {entidad=E001, relación=X-9} |
| AS-02 | {...} | {...} | {... resultado comprobable ...} | R2 | {...} |

## 8. Requerimientos no funcionales

_Anclados en la arquitectura del proyecto (`arquitectura.md`) y en el skill del stack._

| NFR | Métrica | Origen |
|---|---|---|
| Performance | lista carga ≤ 2 s con 50 filas | budget de la arquitectura |
| Seguridad | rol/permiso requerido por operación | matriz de roles del proyecto |
| Integración | {servicio/API} síncrona al guardar | contrato del sistema |
| Disponibilidad / Offline | (aplica / no aplica + por qué) | arquitectura |

## 9. Asunciones

- **A1.** ...
- **A2.** ...

## 10. Preguntas abiertas (no críticas — el flujo avanza igual)

- **Q1.** ...

## 11. Notas del prototipo

| Item | Detalle |
|---|---|
| Vistas en el prototipo | 1:1 con tabla §4 (cada CU es una vista o sub-flujo) |
| Datos simulados | ej. 3 entidades E001/E002/E003, 5 relaciones, 2 usuarios |
| NO incluye | (acotaciones visuales: vistas analíticas, mantenimiento de catálogos, ...) |
| Cómo navegar | "Click en las pestañas superiores; en cada campo con 🔍 se abre el buscador" |

---

## Reglas de redacción (para el agente)

1. **Conciso, no escueto.** Cada sección completa pero sin párrafos que expliquen
   conceptos que el lector del stack ya conoce. Refiere los términos técnicos por
   nombre; no los definas.
2. **Tablas sobre prosa.** Casos de uso, reglas de negocio, NFRs, campos,
   criterios, actores: tabular siempre que sea posible. Una fila = una idea
   completa.
3. **Bullets de una sola línea.** Si necesitas más, parte en sub-bullets.
4. **No repitas info.** Si está en la tabla §4, no lo redactes en §6 también.
5. **No inflar.** Si una sección no aplica, escribe "No aplica" + una frase
   de por qué. No la borres (mantén la estructura).
6. **Lenguaje del cliente, no del consultor.** "El supervisor registra
   las horas" — no "el actor introduce datos en la entidad transaccional".
7. **Casos de uso compactos.** La sección §4 es la guía visual del prototipo
   — debe poderse leer rápido y mapear 1:1 con las vistas.
8. **Convenciones del stack las dicta el skill de diseño.** Nombres de objetos,
   controles de UI válidos, jerga y NFRs típicos vienen del `<skill_de_diseno>`
   del perfil. SIGUE esas convenciones al pie; esta plantilla no las fija para no
   atar la spec a una tecnología.
9. **Términos técnicos — valida la grafía.** La transcripción de audio puede
   traer erratas de speech-to-text. Antes de usar un término técnico, asegúrate
   de la grafía correcta según el stack (ver el skill de diseño). Si la
   transcripción es claramente una errata, corrígela y agrega una asunción **A1.**
   Si es genuinamente ambigua, pon una pregunta abierta **Q1.**
10. **Aseveraciones = contrato SDD (§7).** Los criterios de aceptación son
   aseveraciones atómicas y verificables por máquina (no prosa). El "Entonces"
   debe ser comprobable sin ambigüedad. Emítelas también en el campo
   estructurado `assertions` — es lo que ejecuta el robot de desarrollo.
