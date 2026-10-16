# Skill de diseño — SAP ABAP Clásico · Dynpro / SAP GUI (legacy)

Aplica a la fase de DISEÑO (spec, prototipo, casos de prueba) cuando el **stack
objetivo es ABAP clásico** (mantenimiento de objetos legacy en SAP GUI / Dynpro).
El prototipo debe verse como una **pantalla SAP GUI clásica**, NO Fiori.

## Fidelidad del prototipo (REGLA DURA)

1. CADA vista declara su tipo de pantalla SAP GUI en el comentario:
   `<!-- VISTA: <nombre> | Pantalla: Dynpro selección | Dynpro detalle | ALV Grid | ALV jerárquico | Pop-up | Árbol -->`

2. Usa SOLO controles válidos de SAP GUI clásico, mapeados al toolkit de maqueta `f-*`:
   - Pantalla de selección (SELECT-OPTIONS / PARAMETERS)  → `f-toolbar` + `f-input`/`f-select`
   - Listado tabular (ALV Grid / table control)           → `f-table`
   - Campos de detalle (Dynpro)                           → `f-card` + `f-form`
   - Value help / F4 (search help)                         → `f-mc` + `f-dialog-bg`
   - Botones de toolbar / status GUI                       → `f-btn`
   - Mensajes (MESSAGE) / semáforos de estado              → `f-badge`
   - Pop-ups (POPUP_TO_CONFIRM, etc.)                      → `f-dialog-bg`

3. PROHIBIDO (no existen en SAP GUI clásico): controles Fiori/UI5, **KPI cards /
   tarjetas analíticas — NO uses las clases `f-kpi` ni `f-kpis` (nada de "números
   grandes" en tarjetas)**, gráficos modernos, drag&drop, componentes web, mapas,
   canvas, sliders/diales/gauges; nada que no exista en SAP GUI clásico. Los
   totales/contadores van en una celda de tabla o un campo de formulario, NO en
   una tarjeta KPI. La estética es **gris SAP GUI**, densa y tabular — NO un
   dashboard.

4. Clean-Core: toda capacidad NUEVA debería ir a Fiori; el clásico es SOLO
   mantenimiento legacy justificado. Si el requisito parece una capacidad nueva,
   anótalo: `<!-- NOTA: ¿esto debería ser Fiori (Clean-Core)? confirmar -->`.

## Spec y casos de prueba (notas del stack)

- Documenta el/los objeto(s) legacy afectados (programa/transacción/módulo de
  función Z* o /RISE/*) y, si aplica, el plan de migración a Fiori.
- NFR anclados a la arquitectura. Aseveraciones AS verificables; cada caso de
  prueba referencia al menos una AS.

## Convenciones SAP de la spec (rige sobre la plantilla neutral)

- **§2 Actores/permisos:** chequeo de autorización con `AUTHORITY-CHECK` sobre
  objetos de autorización; declara roles PFCG `Z_BR_AREA_USER` /
  `Z_BR_AREA_ADMIN` o `SAP_BR_*` estándar.
- **§5 Datos:** orígenes vía tablas transparentes / search help Z*; tipos DDIC.
- **§8 NFR:** tiempos de Dynpro / ALV, integración por BAPI/RFC, bloqueos
  (enqueue) si aplica.
- **Nombres de objetos (todo mayúsculas, guión bajo `_`, NUNCA guión medio):**
  - Tablas / estructuras / views: `Z_SUP_TRACTOR` ✅ (no `Z-SUP-TRACTOR` ❌).
  - Roles PFCG: `Z_BR_AREA_USER`, `Z_BR_AREA_ADMIN`.
  - Programas / clases / function modules / transacciones: prefijo `Z`/`Y`
    (`ZCL_FACT_MONTAJE`, `ZBAPI_MONTAJE_CREATE`, `ZMONTAJE01`).
  - Paquetes (devc): `ZBR_FACT_MONTAJE`.
- **Grafía de jerga SAP (corrige erratas de speech-to-text):** `ABAP` (no AVAP),
  `BAPI` (no BAPY), `Dynpro`, `ALV`, `RFC`, `BSP`, `WebDynpro`, `BDC`, `SAP GUI`,
  `S/4HANA`, `EAM`, `PM`, `QM`, `WM`, `HCM`. Si el contexto es claramente SAP,
  corrige y agrega una asunción **A1**; si es ambiguo, una pregunta abierta **Q1**.

## Publicación del paquete (por GIT, NO por API)

Cuando termines —o cuando te pidan **"publicar el diseño"**— publícalo por **git**
desde tu clon, NUNCA con una tool del MCP (ya no existe `publicar_diseno`):

1. Escribe todo el paquete bajo la carpeta **`diseno/`** del repo del requerimiento:
   - `diseno/spec.md` — especificación.
   - `diseno/assertions.json` — aseveraciones **AS** verificables.
   - `diseno/prototipo.html` — prototipo navegable.
   - `diseno/test-plan.md` — casos de prueba.
   - `diseno/soporte/<doc>` — **todo material de apoyo** (cuestionarios al
     funcional, notas, hojas de cálculo, etc.). **Nunca en la raíz del repo.**
2. Publica con git (esto ES la publicación):
   `git add diseno/ && git commit -m "diseño #<wp>" && git push`.
   Para corregir, reescribe el **mismo** archivo y vuelve a hacer push (git lleva
   el historial; no crees carpetas `vN`).
3. El inbox lee el paquete de `diseno/` automáticamente; no hay paso extra de "subir".
4. Para pasar a revisión, usa el MCP `enviar_a_revision(rol='funcional', actor='tu_nombre')`
   o el botón de la compuerta en el inbox.
