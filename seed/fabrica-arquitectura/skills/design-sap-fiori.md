# Skill de diseño — SAP S/4HANA Cloud · Fiori Elements (Clean-Core)

Aplica a la fase de DISEÑO (spec, prototipo, casos de prueba) cuando el **stack
objetivo es SAP Fiori Elements** (Clean-Core). El prototipo debe verse como algo
CONSTRUIBLE con Fiori Elements — UI dirigida por **anotaciones sobre CDS**, no
freestyle, no controles inventados.

## Fidelidad del prototipo (REGLA DURA)

1. CADA vista declara su FLOORPLAN de Fiori Elements en el comentario:
   `<!-- VISTA: <nombre> | Floorplan: List Report | Object Page | Analytical List Page | Overview Page | Form Entry Object Page -->`

2. Usa SOLO patrones expresables por anotaciones, mapeados al toolkit de maqueta `f-*`:
   - Lista / tabla (List Report, tablas de Object Page)  → `f-table`
   - Barra de filtros (SelectionFields)                  → `f-toolbar` + `f-input`/`f-select`
   - Secciones / formulario de detalle (FieldGroup)      → `f-card` + `f-form`
   - Value help / F4 (matchcode)                          → `f-mc` + `f-dialog-bg`
   - Acciones (ligadas a acciones RAP)                    → `f-btn`
   - KPIs / tarjetas analíticas (ALP / OVP)               → `f-kpi`
   - Estado / criticality                                 → `f-badge`

3. PROHIBIDO: inventar controles/widgets que no existen en SAP; editor de texto
   enriquecido, drag&drop, calendario/gantt, mapas, canvas o gráficos dibujados a
   mano, sliders/diales/gauges, wizards no estándar, tree-tables arbitrarias;
   controles de ABAP clásico/Dynpro.

4. Si una interacción que la spec pide NO es expresable con anotaciones de Fiori
   Elements, NO dibujes un control falso. Anótala:
   `<!-- FUERA-DE-FE: <qué> — requeriría SAPUI5 freestyle, justificar en la spec -->`
   y, si es central al requisito, responde `mode="blocked"` preguntando.

## Spec y casos de prueba (notas del stack)

- NFR anclados a la arquitectura del proyecto (Clean-Core, performance OData,
  autorización por entidad/campo, i18n) — nada genérico.
- Toda capacidad nueva pasa primero por Fiori Elements; SAPUI5 freestyle solo si
  se justifica explícitamente en la spec.
- Las aseveraciones AS son verificables (valor/conteo/estado/mensaje exactos) y
  cada caso de prueba referencia al menos una AS.

## Convenciones SAP de la spec (rige sobre la plantilla neutral)

- **§2 Actores/permisos:** authorization-as-code en la **BDEF**; declara roles
  PFCG `Z_BR_AREA_USER` / `Z_BR_AREA_ADMIN` o `SAP_BR_*` estándar; valida lectura
  /escritura por usuario y rol antes de cada operación.
- **§5 Datos:** orígenes vía CDS / matchcode (search help) Z*; tipos DDIC.
- **§8 NFR:** budget RAP (p. ej. lista ≤ 2 s en HEAD), integración por BAPI/OData
  síncrona, i18n por textos de datos.
- **Nombres de objetos (todo mayúsculas, guión bajo `_`, NUNCA guión medio):**
  - Tablas / estructuras / CDS views: `Z_SUP_TRACTOR` ✅ (no `Z-SUP-TRACTOR` ❌).
  - Roles PFCG: `Z_BR_AREA_USER`, `Z_BR_AREA_ADMIN`.
  - Programas / clases / function modules: prefijo `Z`/`Y` (`ZCL_FACT_MONTAJE`,
    `ZBAPI_MONTAJE_CREATE`).
  - Paquetes (devc): `ZBR_FACT_MONTAJE`.
- **Grafía de jerga SAP (corrige erratas de speech-to-text):** `ABAP` (no AVAP),
  `BAPI` (no BAPY), `RAP` (RESTful ABAP Programming, no "wrap"), `CDS`, `BDEF`
  (Behavior Definition), `BTP`, `S/4HANA`, `EAM`, `PM`, `QM`, `WM`, `HCM`,
  `Fiori`, `SAPUI5`, `OData`. Si el contexto es claramente SAP, corrige y agrega
  una asunción **A1**; si es ambiguo, una pregunta abierta **Q1**.

## Catálogo de orígenes de datos (CDS/OData released) — REFERENCIA PREFERENTE

Úsalo como **fuente preferente** para la columna **"Origen (tabla-campo)"** de
`## Datos y campos`. Reglas:

- Si el dato está aquí, usa el objeto del catálogo (no de memoria) y NO lo marques
  "(a confirmar)" salvo el sufijo de campo concreto.
- Si **no** está aquí, propón el objeto **released más probable** marcado
  **"(a confirmar)"** — no bloquees por eso (lo valida el consultor en el SAP API
  Business Hub / VDM). Donde el catálogo diga **(verificar)**, el nombre/versión
  depende del release del cliente: propón y marca "(a confirmar)".
- En S/4HANA el **Universal Journal (ACDOCA)** y los **CDS released `I_*` / OData
  `API_*`** son la vía Clean-Core; las tablas legacy se citan solo como apoyo.

| Área | Objeto | CDS released | OData released | Tabla legacy |
|---|---|---|---|---|
| Maestro | Interlocutor (cliente/proveedor) | `I_BusinessPartner` | `API_BUSINESS_PARTNER` | BUT000, KNA1, LFA1 |
| Maestro | Material / Producto | `I_Product`, `I_ProductPlant`, `I_ProductDescription` | `API_PRODUCT_SRV` | MARA, MARC, MAKT |
| Maestro | Cuenta de mayor | `I_GLAccountInChartOfAccounts`, `I_GLAccountLineItem` | `API_GLACCOUNTINCHARTOFACCOUNTS_SRV` | SKA1, SKB1 |
| Maestro | Centro de coste / beneficio | `I_CostCenter`, `I_ProfitCenter` | (verificar) | CSKS, CEPC |
| Maestro | Centro / Sociedad | `I_Plant`, `I_CompanyCode` | (varios) | T001W, T001 |
| MM | Pedido de compra | `I_PurchaseOrder`, `I_PurchaseOrderItem` | `API_PURCHASEORDER_PROCESS_SRV` | EKKO, EKPO |
| MM | Solicitud de pedido | `I_PurchaseRequisition`, `I_PurchaseRequisitionItem` | `API_PURCHASEREQ_PROCESS_SRV` | EBAN |
| MM | Documento de material (movim.) | `I_MaterialDocumentHeader`, `I_MaterialDocumentItem` | `API_MATERIAL_DOCUMENT_SRV` | MKPF, MSEG |
| MM | Stock | `I_MaterialStock` | `API_MATERIAL_STOCK_SRV` | MARD, MCHB |
| SD | Pedido de venta | `I_SalesOrder`, `I_SalesOrderItem` | `API_SALES_ORDER_SRV` | VBAK, VBAP |
| SD | Factura (incl. intercompany) | `I_BillingDocument`, `I_BillingDocumentItem` | `API_BILLING_DOCUMENT_SRV` | VBRK, VBRP |
| SD | Entrega de salida | `I_OutboundDelivery`, `I_OutboundDeliveryItem` | `API_OUTBOUND_DELIVERY_SRV` | LIKP, LIPS |
| FI | Documento contable / Universal Journal | `I_JournalEntry`, `I_JournalEntryItem` | `API_JOURNALENTRY_SRV` | ACDOCA (BKPF/BSEG legacy) |
| PP | Orden de fabricación | `I_ManufacturingOrder` (verificar; algunos releases `I_ProductionOrder*`) | `API_PRODUCTION_ORDER_2_SRV` | AUFK, AFKO, AFPO |
| PP | Centro de trabajo / capacidad | `I_WorkCenter` (verificar) | (verificar) | CRHD |
| PP | Lista de materiales (BOM) | `I_BillOfMaterial*` (verificar) | `API_BILL_OF_MATERIAL_SRV` (verificar) | STKO, STPO |
| TM | Transporte (embedded TM) | objetos propios de TM (Freight Order/Unit) — **(verificar por release)** | (verificar) | /SCMTMS/* |

> Este catálogo es una **semilla validada por la fábrica**; el consultor lo
> amplía. Para la factura **intercompany** el objeto es el mismo de SD billing
> (`I_BillingDocument`/`I_BillingDocumentItem`, `VBRK`/`VBRP`), distinguida por el
> **tipo de factura** intercompany — no hay un CDS aparte "solo intercompany".

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
